from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shine.h3.compiler import CompiledPrompt, H3PromptCompiler, PromptCompilationError
from shine.models import (
    Asset,
    AuditEvent,
    Job,
    JobDependency,
    Project,
    ProjectTerminology,
    Shot,
    ShotAssetBinding,
    StyleVersion,
)
from shine.schemas import (
    CreateJob,
    ReferenceManifestItem,
    ReferenceUse,
    RenderProvider,
    RenderRequest,
    ShotBindingInput,
    ShotSpec,
    StyleBible,
)
from shine.security import (
    MAX_REFERENCE_COUNTS,
    MAX_REFERENCE_DURATION_S,
    MAX_TOTAL_REFERENCES,
    sanitize_payload,
    sha256_bytes,
    storage_relative_path,
)

JOB_TRANSITIONS = {
    "PLANNED": {"QUEUED", "NEEDS_REVIEW", "WAITING_ASSET"},
    "WAITING_ASSET": {"PLANNED", "NEEDS_REVIEW"},
    "READY": {"QUEUED", "NEEDS_REVIEW"},
    "QUEUED": {"SUBMITTING", "NEEDS_REVIEW"},
    "SUBMITTING": {"RUNNING", "FAILED", "REPAIR_PENDING", "NEEDS_REVIEW"},
    "RUNNING": {"QC_PENDING", "FAILED", "NEEDS_REVIEW"},
    "QC_PENDING": {"PASSED", "REPAIR_PENDING", "NEEDS_REVIEW", "FAILED"},
    "PASSED": {"NEEDS_REVIEW"},
    "REPAIR_PENDING": {"READY", "FAILED", "NEEDS_REVIEW", "QUEUED"},
    # NEEDS_REVIEW may only move forward again through external
    # reconciliation, which proves that the original submission exists.
    # FAILED is reachable only through an explicit local-user abandon decision.
    "NEEDS_REVIEW": {"RUNNING", "FAILED"},
    "FAILED": set(),
}

ACTIVE_JOB_STATUSES = {"QUEUED", "SUBMITTING", "RUNNING", "QC_PENDING"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def add_audit(
    db: Session,
    actor: str,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any] | None = None,
    *,
    project_id: str | None = None,
) -> None:
    resolved_project = project_id or (entity_id if entity_type == "project" else None)
    db.add(
        AuditEvent(
            project_id=resolved_project,
            actor=actor,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=sanitize_payload(payload or {}),
        )
    )


def transition(job: Job, next_status: str) -> None:
    if next_status not in JOB_TRANSITIONS.get(job.status, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Illegal job transition: {job.status} -> {next_status}",
        )
    job.status = next_status
    job.state_version = (job.state_version or 0) + 1


def try_transition(job: Job, next_status: str) -> bool:
    if next_status not in JOB_TRANSITIONS.get(job.status, set()):
        return False
    job.status = next_status
    job.state_version = (job.state_version or 0) + 1
    return True


def require_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def require_shot(db: Session, project_id: str, shot_id: str) -> Shot:
    shot = db.get(Shot, shot_id)
    if not shot or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="Shot not found in this project")
    return shot


def store_asset_file(root: Path, digest: str, content: bytes) -> str:
    relative = storage_relative_path(digest)
    target = root / relative
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            temporary = target.with_name(f"{target.name}.{uuid4().hex}.partial")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
            raise HTTPException(status_code=507, detail="Local storage is full") from exc
        raise HTTPException(status_code=500, detail="Unable to store the uploaded asset") from exc
    return relative


def update_style_draft(db: Session, project: Project, style: StyleBible) -> None:
    project.style_draft = style.model_dump(mode="json")
    add_audit(db, "user", "STYLE_DRAFT_UPDATED", "project", project.id, project_id=project.id)


def _style_hash(snapshot: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(snapshot).encode("utf-8"))


def freeze_style(
    db: Session, project: Project, *, approval_note: str = "", actor: str = "user"
) -> tuple[StyleVersion, dict[str, Any]]:
    if not project.style_draft:
        raise HTTPException(status_code=409, detail="Save all seven style dimensions before freezing")
    snapshot = StyleBible.model_validate(project.style_draft).model_dump(mode="json")
    digest = _style_hash(snapshot)
    previous_id = project.frozen_style_version_id
    previous = db.get(StyleVersion, previous_id) if previous_id else None
    if previous and previous.sha256 == digest:
        raise HTTPException(status_code=409, detail="The style draft is identical to the current frozen version")
    version_number = max((row.version for row in project.style_versions), default=0) + 1
    frozen = StyleVersion(
        project_id=project.id,
        version=version_number,
        snapshot=snapshot,
        sha256=digest,
        approved_by=actor,
        approval_note=approval_note,
    )
    db.add(frozen)
    db.flush()
    project.frozen_style_version_id = frozen.id
    add_audit(
        db,
        actor,
        "STYLE_FROZEN",
        "style_version",
        frozen.id,
        {"sha256": digest, "version": version_number, "previous_version_id": previous_id},
        project_id=project.id,
    )
    impact = analyze_style_impact(db, project, previous_id=previous_id, new_version_id=frozen.id)
    return frozen, impact


def analyze_style_impact(
    db: Session, project: Project, *, previous_id: str | None, new_version_id: str
) -> dict[str, Any]:
    affected_shots = [
        shot
        for shot in db.scalars(select(Shot).where(Shot.project_id == project.id)).all()
        if (shot.spec.get("style_version_id") or "") != new_version_id
    ]
    if previous_id is None:
        return {"previous_style_version_id": None, "affected_shot_ids": [], "invalidated_job_ids": []}
    invalidated: list[str] = []
    for job in db.scalars(select(Job).where(Job.project_id == project.id)).all():
        if job.style_version_id != previous_id:
            continue
        if job.status in {"PASSED", "FAILED", "NEEDS_REVIEW"}:
            continue
        job.stale = True
        job.failure_code = "STYLE_VERSION_SUPERSEDED"
        if try_transition(job, "NEEDS_REVIEW"):
            invalidated.append(job.id)
            add_audit(
                db,
                "orchestrator",
                "JOB_STYLE_SUPERSEDED",
                "job",
                job.id,
                {"previous_version_id": previous_id, "new_version_id": new_version_id},
                project_id=project.id,
            )
    if affected_shots:
        add_audit(
            db,
            "orchestrator",
            "STYLE_IMPACT_ANALYZED",
            "project",
            project.id,
            {
                "affected_shot_count": len(affected_shots),
                "invalidated_job_count": len(invalidated),
            },
            project_id=project.id,
        )
    return {
        "previous_style_version_id": previous_id,
        "affected_shot_ids": sorted(shot.id for shot in affected_shots),
        "invalidated_job_ids": sorted(invalidated),
    }


def validate_shot_style(
    db: Session,
    project_id: str,
    spec: ShotSpec,
    *,
    existing_style_version_id: str | None = None,
    require_current: bool = False,
) -> StyleVersion:
    if not spec.style_version_id:
        raise HTTPException(status_code=422, detail="A shot must reference a frozen style version")
    style = db.get(StyleVersion, spec.style_version_id)
    if not style or style.project_id != project_id:
        raise HTTPException(status_code=422, detail="Shot must reference a frozen style version in this project")
    project = require_project(db, project_id)
    if require_current and style.id != project.frozen_style_version_id and style.id != existing_style_version_id:
        raise HTTPException(
            status_code=422,
            detail="New shots must use the project's current frozen Style Bible; revise the shot to adopt it",
        )
    return style


def _use_as_for_kind(kind: str) -> str:
    return {"image": "picture", "video": "video", "audio": "audio"}[kind]


def _expected_kind(use_as: str) -> str:
    return "image" if use_as in {"subject", "picture"} else use_as


def _validate_binding_inputs(db: Session, project: Project, bindings: list[ShotBindingInput]) -> dict[str, Asset]:
    if len(bindings) > MAX_TOTAL_REFERENCES:
        raise HTTPException(status_code=422, detail="A shot may bind at most 12 reference files")
    asset_ids = [binding.asset_id for binding in bindings]
    if len(set(asset_ids)) != len(asset_ids):
        raise HTTPException(status_code=422, detail="Each asset may appear only once in a shot's bindings")
    assets = (
        {
            asset.id: asset
            for asset in db.scalars(select(Asset).where(Asset.project_id == project.id, Asset.id.in_(asset_ids))).all()
        }
        if asset_ids
        else {}
    )
    if len(assets) != len(asset_ids):
        raise HTTPException(status_code=422, detail="Every bound asset must belong to this project")
    counts = {kind: 0 for kind in MAX_REFERENCE_COUNTS}
    for binding in bindings:
        asset = assets[binding.asset_id]
        if not asset.approved or not asset.locked:
            raise HTTPException(status_code=409, detail="Shot bindings must use user-approved and locked assets")
        if asset.kind != _expected_kind(binding.use_as.value):
            raise HTTPException(status_code=422, detail=f"Binding {binding.asset_id} has an incompatible media type")
        if binding.frame_role and binding.use_as.value != "picture":
            raise HTTPException(status_code=422, detail="Only picture references can carry a frame role")
        counts[asset.kind] += 1
        if counts[asset.kind] > MAX_REFERENCE_COUNTS[asset.kind]:
            raise HTTPException(
                status_code=422,
                detail=f"H3 input limit exceeded for {asset.kind} references (max {MAX_REFERENCE_COUNTS[asset.kind]})",
            )
    return assets


def set_shot_bindings(
    db: Session, project: Project, shot: Shot, bindings: list[ShotBindingInput], *, actor: str = "user"
) -> list[ShotAssetBinding]:
    assets = _validate_binding_inputs(db, project, bindings)
    required = set(ShotSpec.model_validate(shot.spec).required_asset_ids)
    if not required.issubset(set(assets)):
        raise HTTPException(status_code=422, detail="Bindings are missing assets required by the shot")
    for existing in list(shot.bindings):
        db.delete(existing)
    db.flush()
    rows: list[ShotAssetBinding] = []
    for index, binding in enumerate(sorted(bindings, key=lambda item: (item.ordinal, item.asset_id))):
        row = ShotAssetBinding(
            shot_id=shot.id,
            asset_id=binding.asset_id,
            use_as=binding.use_as.value,
            role=binding.role,
            frame_role=binding.frame_role.value if binding.frame_role else None,
            ordinal=index,
        )
        db.add(row)
        rows.append(row)
    add_audit(
        db,
        actor,
        "SHOT_BINDINGS_UPDATED",
        "shot",
        shot.id,
        {"binding_count": len(rows), "asset_ids": sorted(assets)},
        project_id=project.id,
    )
    return rows


def binding_inputs_for_asset_ids(db: Session, project: Project, asset_ids: list[str]) -> list[ShotBindingInput]:
    if not asset_ids:
        return []
    assets = {
        asset.id: asset
        for asset in db.scalars(select(Asset).where(Asset.project_id == project.id, Asset.id.in_(asset_ids))).all()
    }
    if len(assets) != len(set(asset_ids)):
        raise HTTPException(status_code=422, detail="Every required asset must belong to this project")
    return [
        ShotBindingInput(asset_id=asset_id, use_as=ReferenceUse(_use_as_for_kind(assets[asset_id].kind)))
        for asset_id in asset_ids
    ]


def _binding_rows(db: Session, shot: Shot) -> list[ShotAssetBinding]:
    return list(
        db.scalars(
            select(ShotAssetBinding).where(ShotAssetBinding.shot_id == shot.id).order_by(ShotAssetBinding.ordinal)
        ).all()
    )


#: The order in which reference families reach the renderer.  H3 presents all
#: images in one sequence, so a static workflow profile maps its slots only if
#: the references arrive in this family order.  The manifest, the slot numbering
#: and the compiled ``<Picture i>`` indexes all derive from this one constant.
REFERENCE_FAMILY_ORDER = {"subject": 0, "picture": 1, "video": 2, "audio": 3}


def order_bindings_for_cast(
    bindings: list[ShotBindingInput], characters: list[str], assets: dict[str, Asset],
) -> list[ShotBindingInput]:
    """Order references the way the shot describes its cast.

    H3 receives the reference images as one flat sequence, and the compiled
    prompt tells it that "<Subject 1> is the first described person".  That is
    only true when the subject slots follow the shot's own character order, not
    the order the director happened to list the assets in.  Reordering happens
    at request time, so a shot applied by an older build is fixed as well.
    """
    from shine.asset_identity import character_reference_matches

    rank: dict[str, int] = {}
    for index, name in enumerate(characters):
        rank.setdefault(name, index)

    def subject_rank(binding: ShotBindingInput) -> int:
        asset = assets.get(binding.asset_id)
        if asset is None:
            return len(rank)
        for name, index in rank.items():
            if asset.entity_id == name or character_reference_matches(name, asset.entity_id):
                return index
        return len(rank)

    ordered = sorted(
        bindings,
        key=lambda item: (
            REFERENCE_FAMILY_ORDER.get(item.use_as.value, 9),
            subject_rank(item) if item.use_as.value == "subject" else item.ordinal,
            item.ordinal,
        ),
    )
    for index, binding in enumerate(ordered):
        binding.ordinal = index
    return ordered


def build_reference_manifest(bindings: list[ShotBindingInput], assets: dict[str, Asset]) -> list[ReferenceManifestItem]:
    """Build the deterministic, request-local H3 label manifest.

    Official Ref2VA presentation numbers every reference image as ``<Picture i>``
    in request order, so standalone picture labels use the shared image position
    rather than a picture-only counter; subject labels keep their own sequence
    and cite their picture slot inside the compiled prompt.

    The family order is fixed here, not left to the order in which the user
    happened to bind the assets: the workflow maps ``reference_subject_1`` to
    the first image input and ``reference_picture_1`` to a later one, so a
    picture bound before a subject would otherwise make ``<Picture 1>`` point at
    the wrong file - the render would use somebody else's face.
    """
    ordered = sorted(
        bindings,
        key=lambda item: (REFERENCE_FAMILY_ORDER.get(item.use_as.value, 9), item.ordinal, item.asset_id),
    )
    label_counts = {"subject": 0, "video": 0, "audio": 0}
    image_position = 0
    manifest: list[ReferenceManifestItem] = []
    for binding in ordered:
        asset = assets[binding.asset_id]
        use_as = binding.use_as.value
        if use_as in {"subject", "picture"}:
            image_position += 1
        if use_as == "picture":
            label = f"<Picture {image_position}>"
        else:
            label_counts[use_as] += 1
            label_name = {
                "subject": "Subject",
                "video": "Video",
                "audio": "Audio",
            }[use_as]
            label = f"<{label_name} {label_counts[use_as]}>"
        manifest.append(
            ReferenceManifestItem(
                label=label,
                asset_id=asset.id,
                sha256=asset.sha256,
                storage_key=asset.storage_path,
                content_type=asset.content_type,
                use_as=binding.use_as,
                role=binding.role,
                frame_role=binding.frame_role,
                ordinal=binding.ordinal,
                duration_s=asset.duration_s,
                entity_id=asset.entity_id,
            )
        )
    return manifest


def manifest_from_shot_bindings(db: Session, shot: Shot) -> list[ReferenceManifestItem]:
    """Rebuild the manifest from the shot's saved, authoritative bindings."""
    rows = _binding_rows(db, shot)
    if not rows:
        return []
    assets = {
        asset.id: asset for asset in db.scalars(select(Asset).where(Asset.id.in_([row.asset_id for row in rows]))).all()
    }
    bindings = [
        ShotBindingInput(
            asset_id=row.asset_id,
            use_as=row.use_as,
            role=row.role,
            frame_role=row.frame_role,
            ordinal=row.ordinal,
        )
        for row in rows
    ]
    return build_reference_manifest(bindings, assets)


def make_render_request(
    db: Session,
    project: Project,
    shot: Shot,
    body: CreateJob,
    *,
    spec: ShotSpec | None = None,
    continuity_artifact_id: str | None = None,
    offline: bool = False,
) -> tuple[RenderRequest, list[Asset], dict[str, Any]]:
    if not project.frozen_style_version_id:
        raise HTTPException(status_code=409, detail="Freeze the Style Bible before creating a render job")
    spec = spec or ShotSpec.model_validate(shot.spec)
    style = validate_shot_style(db, project.id, spec, require_current=True)
    if body.provider == RenderProvider.COMFYUI and not body.workflow_profile_id:
        raise HTTPException(status_code=409, detail="ComfyUI renders require a registered workflow profile")

    stored = _binding_rows(db, shot)
    requested = body.references
    if requested is not None:
        requested_inputs = [
            ShotBindingInput(
                asset_id=ref.asset_id,
                use_as=ref.use_as,
                role=ref.role,
                frame_role=ref.frame_role,
                ordinal=index,
            )
            for index, ref in enumerate(requested)
        ]
        if stored:
            stored_set = {(row.asset_id, row.use_as) for row in stored}
            requested_set = {(row.asset_id, row.use_as.value) for row in requested_inputs}
            if stored_set != requested_set:
                raise HTTPException(
                    status_code=409,
                    detail="Job references must match this shot's saved bindings to prevent cross-shot asset drift",
                )
        bindings = requested_inputs
    else:
        bindings = [
            ShotBindingInput(
                asset_id=row.asset_id,
                use_as=row.use_as,
                role=row.role,
                frame_role=row.frame_role,
                ordinal=row.ordinal,
            )
            for row in stored
        ]
    assets = _validate_binding_inputs(db, project, bindings)
    if not set(spec.required_asset_ids).issubset(assets):
        raise HTTPException(status_code=422, detail="Render references are missing assets required by the shot")

    # The subject slots must follow the cast order the shot's own description
    # uses; otherwise <Subject 1> can name a different performer than the first
    # person the prompt describes, and the renderer hands one face to another.
    bindings = order_bindings_for_cast(bindings, list(spec.characters), assets)
    if spec.continuity.mode.value != "cut" and body.provider == RenderProvider.COMFYUI and not offline:
        from shine.media.continuity import find_continuity_artifact, load_continuity_reference

        mode = spec.continuity.mode.value
        source_job_id = spec.continuity.previous_job_id
        if not source_job_id:
            raise HTTPException(409, "CONTINUITY_SOURCE_REQUIRED")
        artifact_id = continuity_artifact_id
        if artifact_id is None:
            # A repair or a resumed Worker reuses the derivative that already
            # exists for this source; it never derives a second copy of the
            # same bytes, and it never renders without a real reference.
            existing = find_continuity_artifact(
                db, project.id, source_job_id, mode,
                start_s=spec.continuity.reference_start_s,
                end_s=spec.continuity.reference_end_s,
            )
            if existing is None:
                raise HTTPException(409, "CONTINUITY_DERIVATIVE_REQUIRED")
            artifact_id = existing.id
        try:
            ref = load_continuity_reference(db, project.id, artifact_id, source_job_id, mode)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        lineage = ref.provenance
        for field, key in ((spec.continuity.reference_start_s, "start_s"),
                           (spec.continuity.reference_end_s, "end_s")):
            if field is not None and field != lineage.get(key):
                raise HTTPException(409, "CONTINUITY_REFERENCE_WINDOW_CHANGED")
        family = {"last_frame": "picture", "hard_frame_anchor": "picture",
                  "video_reference": "video", "audio_reference": "audio"}.get(mode)
        if not family:
            raise HTTPException(409, "CONTINUITY_MODE_UNSUPPORTED")
        if len(bindings) >= 12:
            raise HTTPException(422, "CONTINUITY_REFERENCE_LIMIT_EXCEEDED")
        assets[ref.id] = ref
        bindings.append(ShotBindingInput(
            asset_id=ref.id, use_as=family, role="continuity_derivative",
            frame_role="first_frame" if mode == "hard_frame_anchor" else None,
            ordinal=len(bindings),
        ))
    ordered = sorted(bindings, key=lambda item: (item.ordinal, item.asset_id))
    manifest = build_reference_manifest(ordered, assets)

    from shine.h3.rulebook import H3RulebookRegistry

    registry = H3RulebookRegistry()
    try:
        pack = registry.for_project(db, project.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    compiler = H3PromptCompiler()
    try:
        compiled: CompiledPrompt = compiler.compile(
            spec=spec,
            style=StyleBible.model_validate(style.snapshot),
            manifest=manifest,
            project_language=project.language,
            mode_hint=body.generation_mode,
        )
    except PromptCompilationError as exc:
        if pack:
            raise HTTPException(
                status_code=422, detail=registry.compilation_failure(pack, exc).model_dump(mode="json")
            ) from exc
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc), "details": exc.details},
        ) from exc

    workflow_hash = None
    transfer_warning_lines: list[str] = []
    if body.workflow_profile_id:
        from shine.models import WorkflowProfile

        profile = db.get(WorkflowProfile, body.workflow_profile_id)
        if not profile or (profile.project_id and profile.project_id != project.id):
            raise HTTPException(status_code=404, detail="Workflow profile not found for this project")
        if profile.mode.lower() != compiled.mode.value.lower():
            raise HTTPException(status_code=422, detail="Workflow profile does not support the compiled H3 mode")
        workflow_hash = profile.graph_sha256
        from shine.comfy.capabilities import HARD, VIDEO, continuity_capability, profile_metadata

        # A requested output size only reaches the renderer through an explicit
        # logical mapping.  Refusing here keeps the user's choice honest: the
        # job either renders at the size they asked for or it does not start.
        if body.output is not None and body.output.width is not None:
            unmapped = [
                name for name in ("width", "height")
                if name not in (profile.parameter_map or {})
            ]
            if unmapped:
                raise HTTPException(status_code=422, detail={
                    "code": "WORKFLOW_SIZE_NOT_MAPPED",
                    "message": "The selected workflow profile cannot apply the requested output size",
                    "details": {
                        "missing_parameters": unmapped,
                        "mapped_parameters": sorted(profile.parameter_map or {}),
                    },
                })
        capability = continuity_capability(spec.continuity.mode.value)
        capabilities = profile_metadata(profile.graph, profile.mode, profile.parameter_map, profile.media_transfers)
        if capability and capability not in capabilities["continuity_modes"]:
            code = "PENDING_HARD_CONTINUITY_TEST" if capability == HARD else "PENDING_WORKFLOW_MAPPING"
            if capability == VIDEO:
                code = "SUPPORTED_NODE_UNVERIFIED_TRANSFER"
            raise HTTPException(status_code=422, detail={"code": code, "requested_continuity": capability})
        # The other direction matters too: a profile that declares a media
        # transfer while the request binds no reference of that family.  This
        # no longer refuses the render: the submitted graph deterministically
        # prunes the unused loader, so the shot still renders.  It is surfaced
        # as a warning ("voice is optional - H3 speaks a dialogue line
        # natively"), and the user decides from the warning whether to bind a
        # reference first.  A reference the profile has no slot for is still a
        # hard refusal above: that would silently drop a user-approved asset.
        from shine.comfy.provider import collect_unbound_transfer_families, unbound_transfer_family_warning

        transfer_warnings = collect_unbound_transfer_families(profile.media_transfers, manifest, assets)
        transfer_warning_lines = [unbound_transfer_family_warning(family) for family in transfer_warnings]

    render_request = RenderRequest(
        model_checkpoint_id=profile.checkpoint_id if body.workflow_profile_id else "MiniMax-H3-Base",
        project_language=project.language,
        idempotency_key=uuid4().hex,
        shot_id=shot.id,
        shot_revision=shot.revision,
        style_version_id=style.id,
        style_hash=style.sha256,
        provider=body.provider.value,
        generation_mode=compiled.mode,
        reference_manifest=manifest,
        shot_spec=spec,
        style_snapshot=StyleBible.model_validate(style.snapshot),
        compiled_prompt=compiled.prompt,
        compiled_prompt_sections=compiled.sections,
        prompt_sha256=compiled.prompt_sha256,
        prompt_skill_hash=compiled.skill_sha256,
        compiler_version=compiled.compiler_version,
        rule_version=compiled.rule_version,
        workflow_profile_hash=workflow_hash,
        expected_duration_s=spec.duration_target_s,
        output_width=body.output.width if body.output else None,
        output_height=body.output.height if body.output else None,
        self_review_scope=body.self_review_scope,
        basic_review_resolution=body.basic_review_resolution,
    )
    # V1.1 projects explicitly freeze their rule pack. Legacy jobs retain the
    # original pinned compiler and are never silently enrolled in a new policy.
    if pack:
        render_request = render_request.model_copy(update={"rulepack_id": pack.id, "rulepack_hash": pack.sha256})
        report = registry.validate(render_request, pack)
        if not report.passed:
            raise HTTPException(status_code=422, detail=report.model_dump(mode="json"))
    if not stored and bindings:
        set_shot_bindings(db, project, shot, bindings)
    compiler_meta = {
        "mode": compiled.mode.value,
        "warnings": [*compiled.warnings, *transfer_warning_lines],
        "labels": compiled.labels,
        "rule_version": compiled.rule_version,
    }
    return render_request, list(assets.values()), compiler_meta


def dependencies_for_new_job(
    db: Session, project_id: str, dependency_ids: list[str], *, kinds: dict[str, str] | None = None
) -> tuple[list[Job], str]:
    if len(set(dependency_ids)) != len(dependency_ids):
        raise HTTPException(status_code=422, detail="Job dependencies must be unique")
    parents = list(db.scalars(select(Job).where(Job.id.in_(dependency_ids))).all()) if dependency_ids else []
    if len(parents) != len(dependency_ids) or any(parent.project_id != project_id for parent in parents):
        raise HTTPException(status_code=422, detail="Job dependencies must belong to this project")
    if any(parent.status in {"FAILED", "NEEDS_REVIEW"} or parent.stale for parent in parents):
        raise HTTPException(status_code=409, detail="A dependency is blocked or stale")
    status = "QUEUED" if all(parent.status == "PASSED" for parent in parents) else "PLANNED"
    return parents, status


def project_terminology_hint(db: Session, project_id: str) -> str:
    """The script's terminology, as the hint the ASR model is given.

    The newest revision is the one that is used.  A project without a table
    produces no hint at all, which is exactly what the recognizer expects.
    The table is a hint and never a proof: it does not change any transcript.
    """
    from shine.quality.terminology import TerminologyError, parse_table

    row = db.scalar(
        select(ProjectTerminology)
        .where(ProjectTerminology.project_id == project_id)
        .order_by(ProjectTerminology.revision.desc())
        .limit(1)
    )
    if row is None:
        return ""
    try:
        table = parse_table(list(row.terms or []), row.notes or "")
    except TerminologyError:
        # A malformed table must not silently become a wrong hint.
        return ""
    return table.hint()


def save_project_terminology(
    db: Session,
    project: Project,
    *,
    terms: list[dict[str, Any]],
    notes: str = "",
    created_by: str,
    source: str = "human",
) -> ProjectTerminology:
    """Store a new revision of the script's terminology.

    Every revision is kept.  A production that already ran keeps the table it
    actually used, so the evidence of a run never changes afterwards.
    """
    from shine.quality.terminology import TerminologyError, parse_table

    try:
        table = parse_table(terms, notes)
    except TerminologyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    current = db.scalar(
        select(ProjectTerminology)
        .where(ProjectTerminology.project_id == project.id)
        .order_by(ProjectTerminology.revision.desc())
        .limit(1)
    )
    revision = (current.revision + 1) if current else 1
    row = ProjectTerminology(
        project_id=project.id,
        revision=revision,
        terms=[{"canonical": term.canonical, "aliases": list(term.aliases)} for term in table.terms],
        notes=table.notes,
        sha256=table.sha256(),
        source=source,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def resolve_continuity_source(
    db: Session,
    project_id: str,
    spec: ShotSpec,
    *,
    provider: RenderProvider,
) -> tuple[str | None, str]:
    """Resolve the continuity source job of a shot against the current ledger.

    A non-cut shot may only consume a job of the same project that has already
    passed, is not stale, still matches the current shot revision and — for a
    real ComfyUI render — owns a real approved artifact.  Nothing here is
    inferred: an unresolvable source is a hard error, never a silent cut.
    """
    continuity = spec.continuity
    if continuity.mode.value == "cut":
        return None, "cut"
    previous_job_id = continuity.previous_job_id
    if not previous_job_id:
        raise HTTPException(status_code=422, detail="Continuity modes require a previous_job_id")
    previous = db.get(Job, previous_job_id)
    if not previous or previous.project_id != project_id:
        raise HTTPException(status_code=422, detail="Continuity source job must belong to this project")
    if continuity.previous_shot_id and previous.shot_id != continuity.previous_shot_id:
        raise HTTPException(status_code=422, detail="Continuity source job does not match the previous shot")
    if previous.status != "PASSED" or previous.stale:
        raise HTTPException(status_code=409, detail="Continuity requires a passed, non-stale source job")
    previous_shot = db.get(Shot, previous.shot_id)
    if not previous_shot or previous.shot_revision != previous_shot.revision:
        raise HTTPException(status_code=409, detail="Continuity source job is stale after a shot revision")
    if provider == RenderProvider.COMFYUI and not previous.artifact_id:
        raise HTTPException(
            status_code=409,
            detail="ComfyUI continuity requires a real approved artifact from the source job",
        )
    return previous_job_id, f"continuity:{continuity.mode.value}"


def create_render_job(
    db: Session,
    project: Project,
    shot: Shot,
    body: CreateJob,
    *,
    settings,
    offline: bool = False,
    spec: ShotSpec | None = None,
    depends_on: list[str] | None = None,
    continuity_artifact_id: str | None = None,
) -> tuple[Job, dict[str, Any]]:
    """Create one render job for a shot, with every production gate applied.

    This is the single job-creation path.  The HTTP API and the resident
    Worker both call it, so a job queued by the Worker obeys exactly the same
    asset, style, dependency and pre-production rules as a job queued by a
    person in the Web workbench.
    """
    spec = spec or ShotSpec.model_validate(shot.spec)
    if body.provider == RenderProvider.COMFYUI and not offline:
        for binding in shot.bindings:
            if (binding.asset.provenance or {}).get("offline_only"):
                raise HTTPException(409, "MOCK_ASSET_NOT_PRODUCTION")

    dependency_ids = list(depends_on if depends_on is not None else body.depends_on)
    dependency_kinds = {dependency_id: "explicit" for dependency_id in dependency_ids}
    previous_job_id, continuity_kind = resolve_continuity_source(
        db, project.id, spec, provider=body.provider,
    )
    if previous_job_id:
        if previous_job_id not in dependency_ids:
            dependency_ids.append(previous_job_id)
        dependency_kinds[previous_job_id] = continuity_kind

    request_data, _assets, compiler_meta = make_render_request(
        db, project, shot, body, spec=spec, continuity_artifact_id=continuity_artifact_id, offline=offline,
    )
    if body.provider == RenderProvider.COMFYUI:
        from shine.quality.input_assessment import assessment_for_request

        assessment = assessment_for_request(
            db, project.id, request_data, select_latest=True, offline=offline,
        )
        if assessment:
            request_data = request_data.model_copy(update={
                "style_input_assessment_id": assessment.id,
                "style_input_assessment_sha256": assessment.sha256,
            })
    from shine.comfyops.preproduction import enforce

    preproduction_hash = enforce(
        db,
        project.id,
        render_request=request_data,
        offline=(body.provider == RenderProvider.MOCK or offline),
        data_dir=settings.data_dir,
        workflow_profile_id=body.workflow_profile_id if body.provider == RenderProvider.COMFYUI else None,
    )
    parents, initial_status = dependencies_for_new_job(db, project.id, dependency_ids)
    job = Job(
        project_id=project.id,
        shot_id=shot.id,
        shot_revision=shot.revision,
        style_version_id=request_data.style_version_id,
        preproduction_manifest_hash=preproduction_hash,
        request=request_data.model_dump(mode="json"),
        idempotency_key=request_data.idempotency_key,
        status=initial_status,
        provider=body.provider.value,
        mock_scenario=body.mock_scenario.value,
        generation_mode=request_data.generation_mode.value,
        workflow_profile_id=body.workflow_profile_id,
        compiled_prompt_sha256=request_data.prompt_sha256,
        max_semantic_retries=(
            body.max_semantic_retries if body.max_semantic_retries is not None else 2
        ),
        auto_repair=body.auto_repair,
    )
    db.add(job)
    db.flush()
    for parent in parents:
        db.add(
            JobDependency(
                parent_job_id=parent.id,
                child_job_id=job.id,
                kind=dependency_kinds.get(parent.id, "explicit"),
            )
        )
    add_audit(
        db,
        "user",
        "MOCK_JOB_CREATED" if body.provider == RenderProvider.MOCK else "COMFY_JOB_CREATED",
        "job",
        job.id,
        {
            "provider": body.provider.value,
            "mode": request_data.generation_mode.value,
            "dependency_count": len(parents),
            "preproduction_manifest_hash": preproduction_hash,
            "style_hash": request_data.style_hash,
            "prompt_sha256": request_data.prompt_sha256,
            "warnings": compiler_meta.get("warnings", []),
        },
        project_id=project.id,
    )
    return job, compiler_meta


def mark_stale_descendants(db: Session, starting_job_ids: list[str], *, project_id: str | None = None) -> set[str]:
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
        if job.status in {"PLANNED", "READY", "QUEUED", "PASSED", "REPAIR_PENDING", "WAITING_ASSET"}:
            try_transition(job, "NEEDS_REVIEW")
        add_audit(
            db,
            "orchestrator",
            "JOB_DEPENDENCY_INVALIDATED",
            "job",
            job.id,
            project_id=project_id or job.project_id,
        )
    return affected


def validate_reference_durations(manifest: list[ReferenceManifestItem]) -> None:
    totals = {"video": 0.0, "audio": 0.0}
    for item in manifest:
        if item.use_as.value not in totals:
            continue
        duration = item.duration_s
        if duration is None:
            raise HTTPException(
                status_code=422,
                detail=f"Reference {item.label} has unknown duration; probe the media before production",
            )
        if duration < 2 or duration > MAX_REFERENCE_DURATION_S:
            raise HTTPException(status_code=422, detail=f"Reference {item.label} must be between 2 and 15 seconds")
        totals[item.use_as.value] += duration
    for kind, total in totals.items():
        if total > MAX_REFERENCE_DURATION_S:
            raise HTTPException(status_code=422, detail=f"Total {kind} reference duration exceeds 15 seconds")


def create_shot_record(
    db: Session,
    project: Project,
    *,
    name: str,
    spec: ShotSpec,
    bindings: list[ShotBindingInput] | None = None,
    actor: str = "user",
) -> Shot:
    if not project.frozen_style_version_id:
        raise HTTPException(status_code=409, detail="Freeze the Style Bible before creating production shots")
    if not spec.style_version_id:
        spec = spec.model_copy(update={"style_version_id": project.frozen_style_version_id})
    # A manually written shot has the same frozen Style Bible as the AI
    # storyboard shots but no English style wording of its own. Inherit the
    # exact English rendition already accepted for this style version instead
    # of leaking the Chinese Style Bible text into the H3 prompt.
    inherited_from = None
    if spec.prompt_style is None:
        for candidate in db.scalars(
            select(Shot).where(Shot.project_id == project.id).order_by(Shot.created_at.desc())
        ).all():
            try:
                prior = ShotSpec.model_validate(candidate.spec)
            except ValueError:
                continue
            if prior.style_version_id == spec.style_version_id and prior.prompt_style is not None:
                spec = spec.model_copy(update={"prompt_style": prior.prompt_style})
                inherited_from = candidate.id
                break
    validate_shot_style(db, project.id, spec, require_current=True)
    binding_inputs = list(bindings or [])
    if not binding_inputs and spec.required_asset_ids:
        binding_inputs = binding_inputs_for_asset_ids(db, project, spec.required_asset_ids)
    if spec.required_asset_ids:
        required = list(
            db.scalars(
                select(Asset).where(
                    Asset.project_id == project.id,
                    Asset.id.in_(spec.required_asset_ids),
                    Asset.approved.is_(True),
                    Asset.locked.is_(True),
                )
            ).all()
        )
        if len(required) != len(set(spec.required_asset_ids)):
            raise HTTPException(status_code=409, detail="Shot requires user-approved, locked assets")
    if binding_inputs:
        bound = _validate_binding_inputs(db, project, binding_inputs)
        if not set(spec.required_asset_ids).issubset(bound):
            raise HTTPException(status_code=422, detail="Shot bindings are missing assets required by the shot")
    shot = Shot(project_id=project.id, name=name, spec=spec.model_dump(mode="json"))
    db.add(shot)
    db.flush()
    if binding_inputs:
        set_shot_bindings(db, project, shot, binding_inputs, actor=actor)
    add_audit(
        db,
        actor,
        "SHOT_CREATED",
        "shot",
        shot.id,
        {
            "revision": shot.revision,
            "binding_count": len(binding_inputs),
            "prompt_style_inherited_from": inherited_from,
        },
        project_id=project.id,
    )
    return shot


def revise_shot_record(
    db: Session,
    project: Project,
    shot: Shot,
    *,
    spec: ShotSpec,
    bindings: list[ShotBindingInput] | None = None,
    actor: str = "user",
) -> dict[str, Any]:
    previous_style_id = ShotSpec.model_validate(shot.spec).style_version_id
    if not spec.style_version_id:
        spec = spec.model_copy(update={"style_version_id": previous_style_id or project.frozen_style_version_id})
    validate_shot_style(db, project.id, spec, existing_style_version_id=previous_style_id)
    if spec.required_asset_ids:
        approved_count = len(
            db.scalars(
                select(Asset.id).where(
                    Asset.project_id == project.id,
                    Asset.id.in_(spec.required_asset_ids),
                    Asset.approved.is_(True),
                    Asset.locked.is_(True),
                )
            ).all()
        )
        if approved_count != len(set(spec.required_asset_ids)):
            raise HTTPException(status_code=409, detail="Shot requires user-approved, locked assets")
    old_jobs = list(db.scalars(select(Job).where(Job.shot_id == shot.id)).all())
    old_ids = [job.id for job in old_jobs]
    shot.revision += 1
    shot.spec = spec.model_dump(mode="json")
    for job in old_jobs:
        job.stale = True
        job.failure_code = "SHOT_REVISION_REPLACED"
        if job.status not in {"FAILED", "NEEDS_REVIEW"}:
            try_transition(job, "NEEDS_REVIEW")
    affected = mark_stale_descendants(db, old_ids, project_id=project.id)
    if bindings is not None:
        set_shot_bindings(db, project, shot, bindings, actor=actor)
    add_audit(
        db,
        actor,
        "SHOT_REVISED",
        "shot",
        shot.id,
        {"revision": shot.revision, "invalidated_job_ids": sorted(affected)},
        project_id=project.id,
    )
    return {
        "id": shot.id,
        "revision": shot.revision,
        "invalidated_job_ids": sorted(affected),
        "stale_source_job_ids": old_ids,
        "spec": shot.spec,
    }


def job_fingerprint(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()

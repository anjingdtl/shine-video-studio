from __future__ import annotations

import hashlib
import re

from fastapi import HTTPException
from sqlalchemy import literal_column, select

from shine.comfy.workflow import graph_hash
from shine.comfyops.assets import FAMILIES, require_rights
from shine.comfyops.models import (
    PLAN_PURPOSE_ASSET_PREPARATION,
    PLAN_PURPOSE_PRODUCTION,
    PLAN_PURPOSES,
    AssetRequirement,
    CandidateAsset,
    ModelPackage,
    PreProductionPlan,
    RightsRecord,
    VoiceProfile,
    WorkflowRevision,
)
from shine.models import Asset, Project, ProjectDocument, ProjectRuleLock, StyleVersion, WorkflowProfile


def analyze(db, project, document, extras, h3_category="H3_REF2VA", review_scope="full"):
    if document.project_id != project.id:
        raise HTTPException(409, "CROSS_PROJECT_REFERENCE")
    # Conservative deterministic extraction. Explicit script annotations cover non-dialogue entities.
    requirements = []
    speakers = sorted(set(re.findall(r"^([^\n：:]{1,30})[：:]\s*[^\n]+", document.text, re.M)))
    speakers = [x.strip() for x in speakers if not x.startswith(("场景", "scene", "#", "道具", "服装", "音效", "音乐"))]
    for speaker in speakers:
        for family in ("character", "costume", "voice"):
            requirements.append({"family": family, "entity_id": speaker, "critical": True})
    for label, family in [("场景", "scene"), ("道具", "prop"), ("服装", "costume"), ("音效", "sfx"), ("音乐", "music")]:
        for entity in re.findall(
            (label + (r"[一二三四五六七八九十0-9]*" if label == "场景" else "") + r"[：:]\s*([^\n]+)"), document.text
        ):
            requirements.append({"family": family, "entity_id": entity.strip(), "critical": True})
    for family in ("storyboard", "keyframe", "action", "dialogue", "sfx", "music", "subtitle"):
        requirements.append({"family": family, "entity_id": "project", "critical": False})
    structured = None
    if document.parsed and any(k in document.parsed for k in ("scenes", "shots", "beats")):
        from shine.comfyops.requirements import extract_structured

        requirements, structured = extract_structured(document.parsed)
    requirements += extras
    unique = {}
    for requirement in requirements:
        key = (requirement["family"], requirement["entity_id"])
        previous = unique.get(key, {})
        unique[key] = {
            **previous,
            **requirement,
            "critical": previous.get("critical", False) or requirement["critical"],
        }
    for r in unique.values():
        if r["family"] not in FAMILIES:
            raise HTTPException(422, "UNKNOWN_ASSET_FAMILY")
    payload = {
        "document_id": document.id,
        "structured_script": structured,
        "h3_category": h3_category,
        "script_hash": document.sha256,
        "requirements": list(unique.values()),
        "manifest": None,
        "offline": True,
        "style_input_contract": "independent-text-v1",
        **({"review_scope": "basic"} if review_scope == "basic" else {}),
    }
    plan = PreProductionPlan(
        project_id=project.id,
        payload=payload,
        sha256=graph_hash(payload),
        status="ANALYZED",
        purpose=PLAN_PURPOSE_PRODUCTION,
    )
    db.add(plan)
    db.flush()
    ids = {}
    for r in unique.values():
        row = AssetRequirement(
            project_id=project.id, plan_id=plan.id, payload=r, sha256=graph_hash(r), status="MISSING"
        )
        db.add(row)
        db.flush()
        ids[(r["family"], r["entity_id"])] = row.id
    for row in db.scalars(select(AssetRequirement).where(AssetRequirement.plan_id == plan.id)).all():
        r = row.payload
        deps = []
        character = r.get("character_id", r["entity_id"])
        if r["family"] == "costume" and ("character", character) in ids:
            deps = [ids[("character", character)]]
        if r["family"] == "dialogue":
            deps = [value for (family, _), value in ids.items() if family == "voice"]
        if r["family"] in {"storyboard", "keyframe"}:
            deps = [value for (family, _), value in ids.items() if family in {"character", "costume", "scene", "prop"}]
        row.payload = {**r, "depends_on": deps, "strategy": "GENERATE_CANDIDATE_OR_IMPORT"}
        row.sha256 = graph_hash(row.payload)
    return plan


def current_plan_for_purpose(db, project_id, purpose):
    """Return the newest plan of one purpose.

    Two plans written inside one host clock tick can share a timestamp, so the
    row id is only a last resort. The table row id is the real insertion order
    and is used as the tie-break, exactly as elsewhere in this codebase.
    """
    if purpose not in PLAN_PURPOSES:
        raise HTTPException(422, "UNKNOWN_PLAN_PURPOSE")
    return db.scalar(
        select(PreProductionPlan)
        .where(PreProductionPlan.project_id == project_id, PreProductionPlan.purpose == purpose)
        .order_by(
            PreProductionPlan.created_at.desc(),
            literal_column("preproduction_plans.rowid").desc(),
        )
        .limit(1)
    )


def current_plan(db, project_id):
    """Return the newest analysed production plan.

    A director-side material-preparation plan shares this table but is not a
    production contract, so it is never returned here. Freezing, readiness,
    enforce and every H3 gate read through this selector.
    """
    return current_plan_for_purpose(db, project_id, PLAN_PURPOSE_PRODUCTION)


def current_material_plan(db, project_id):
    """Return the newest asset-preparation plan revision."""
    return current_plan_for_purpose(db, project_id, PLAN_PURPOSE_ASSET_PREPARATION)


def require_plan_for_purpose(db, plan_id, purpose):
    """Fetch a plan by id and prove its purpose before any by-id operation.

    A grant, a freeze or a claim addressed at a production plan id must not be
    satisfied by a material plan that happens to be newer, and vice versa.
    """
    plan = db.get(PreProductionPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "PREPRODUCTION_PLAN_NOT_FOUND")
    if plan.purpose != purpose:
        raise HTTPException(409, "PREPRODUCTION_PLAN_PURPOSE_MISMATCH")
    return plan


def readiness(db, project_id, *, offline=True, data_dir=None):
    project = db.get(Project, project_id)
    plan = current_plan(db, project_id)
    if not project or not plan:
        raise HTTPException(409, "PREPRODUCTION_NOT_ANALYZED")
    assets = db.scalars(
        select(Asset).where(Asset.project_id == project_id, Asset.approved.is_(True), Asset.locked.is_(True))
    ).all()
    voices = db.scalars(
        select(VoiceProfile)
        .where(VoiceProfile.project_id == project_id, VoiceProfile.status == "FROZEN")
        .order_by(
            VoiceProfile.created_at.desc(),
            literal_column("voice_profiles.rowid").desc(),
        )
    ).all()
    links, blockers, requirements = [], [], []
    for req in db.scalars(select(AssetRequirement).where(AssetRequirement.plan_id == plan.id)).all():
        r = req.payload
        if r.get("satisfaction") == "LOCKED_SCRIPT":
            links.append(
                {
                    "requirement": req.id,
                    "id": plan.payload["document_id"],
                    "hash": r["line_hash"],
                    "rights": "PROJECT_LOCKED_SCRIPT",
                }
            )
            requirements.append({"id": req.id, **r, "status": "SATISFIED", "reason": None})
            continue
        candidates = db.scalars(
            select(CandidateAsset).where(CandidateAsset.project_id == project_id, CandidateAsset.status == "PROMOTED")
        ).all()
        if r["family"] == "voice":
            match = next((v for v in voices if v.payload["character"] == r["entity_id"]), None)
        else:
            match = next((a for a in assets if a.role == r["family"] and a.entity_id == r["entity_id"]), None)
            if not match:
                match = next(
                    (
                        c
                        for c in candidates
                        if c.payload["family"] == r["family"] and c.payload["entity_id"] == r["entity_id"]
                    ),
                    None,
                )
        reason = None
        if match:
            if isinstance(match, Asset):
                provenance = match.provenance or {}
                mock = provenance.get("offline_only", False) or provenance.get("is_mock", False)
                rights_id = provenance.get("rights_id")
                if data_dir:
                    from shine.media.artifacts import safe_asset_path

                    try:
                        media = safe_asset_path(data_dir, match.storage_path).read_bytes()
                        if hashlib.sha256(media).hexdigest() != match.sha256:
                            reason = "ASSET_HASH_CHANGED"
                    except OSError:
                        reason = "ASSET_FILE_MISSING"
            else:
                mock = match.payload.get("is_mock", match.payload.get("provenance", {}).get("is_mock", False))
                rights_id = match.payload.get("rights_id")
            rights = db.get(RightsRecord, rights_id) if rights_id else None
            if rights_id and (not rights or rights.status != "APPROVED"):
                reason = "LICENSE_NOT_APPROVED"
            if mock and not offline:
                reason = "MOCK_ASSET_NOT_PRODUCTION"
            links.append(
                {
                    "requirement": req.id,
                    "id": match.id,
                    "hash": match.sha256,
                    "rights": rights.sha256 if rights else "LEGACY_USER_APPROVED",
                    "provenance_hash": graph_hash(match.provenance or {}) if isinstance(match, Asset) else match.sha256,
                    "asset_metadata_hash": asset_metadata_hash(match) if isinstance(match, Asset) else None,
                }
            )
        else:
            reason = "ASSET_OR_VOICE_MISSING"
        requirements.append(
            {
                "id": req.id,
                **r,
                "status": "SATISFIED" if match and not reason else "MISSING" if r["critical"] else "OPTIONAL",
                "reason": reason,
            }
        )
        if reason and r["critical"]:
            blockers.append({"requirement": req.id, "code": reason, "entity": r["entity_id"], "family": r["family"]})
    style = db.get(StyleVersion, project.frozen_style_version_id) if project.frozen_style_version_id else None
    lock = db.get(ProjectRuleLock, project_id)
    if not style:
        blockers.append({"code": "STYLE_NOT_FROZEN"})
    if not lock:
        blockers.append({"code": "RULEPACK_NOT_FROZEN"})
    workflows = db.scalars(
        select(WorkflowRevision).order_by(
            WorkflowRevision.created_at, literal_column("workflow_revisions.rowid")
        )
    ).all()
    state = "FAKE_EXECUTED" if offline else "PRODUCTION_APPROVED"
    usable = []
    frozen_ids = {w["id"] for w in (plan.payload.get("manifest") or {}).get("workflows", [])}
    has_frozen_manifest = plan.payload.get("manifest") is not None
    for category in (plan.payload.get("h3_category", "H3_REF2VA"), "VLM_REVIEW", "ASR"):
        choices = [
            w
            for w in workflows
            if w.payload["category"] == category and w.status == state
            and (not has_frozen_manifest or w.id in frozen_ids)
            and (offline or category != "VLM_REVIEW"
                 or ((w.payload.get("native_provider") or {}).get("review_scope") == "basic_character")
                 == (plan.payload.get("review_scope", "full") == "basic"))
        ]
        if not choices:
            blockers.append({"code": "CAPABILITY_MISSING", "category": category})
        else:
            selected, selected_errors = choices[0], []
            if not offline:
                from shine.comfyops.native import native_errors

                def capability_errors(candidate, selected_category=category):
                    errors = native_errors(db, candidate.payload, live=True, require_evidence=True,
                                           workflow_hash=candidate.sha256)
                    if selected_category in {"VLM_REVIEW", "ASR"} and not candidate.payload.get("native_provider"):
                        errors = ["PRODUCTION_REVIEW_PROVIDER_BINDING_REQUIRED", *errors]
                    return errors

                # An unfrozen plan may choose another qualified revision when
                # an older one is stale. A frozen plan can never silently
                # substitute a different capability for its pinned revision.
                selected_errors = capability_errors(selected)
                if selected_errors and not has_frozen_manifest:
                    for candidate in choices[1:]:
                        errors = capability_errors(candidate)
                        if not errors:
                            selected, selected_errors = candidate, errors
                            break
                blockers.extend({"code": error, "category": category, "workflow": selected.id}
                                for error in selected_errors)
            usable.append(selected)
    models = []
    selected_models = {
        identity for w in usable for identity in w.payload["model_ids"] + w.payload.get("node_package_ids", [])
    }
    for workflow in usable:
        if not offline and not workflow.payload["model_ids"]:
            blockers.append({"code": "MODEL_DECLARATION_REQUIRED", "workflow": workflow.id})
    for identity in sorted(selected_models):
        from shine.comfyops.models import NodePackage

        model = db.get(ModelPackage, identity) or db.get(NodePackage, identity)
        allowed = {"MOCK_AVAILABLE"} if offline else {"AVAILABLE", "FILES_VERIFIED_PENDING_RUNTIME"}
        if model and any(
            w.payload.get("package_hashes", {}).get(identity) != model.sha256
            for w in usable
            if identity in w.payload["model_ids"] + w.payload.get("node_package_ids", [])
        ):
            blockers.append({"code": "DEPENDENCY_SNAPSHOT_CHANGED", "model": identity})
        if not model or model.status not in allowed:
            blockers.append({"code": "MODEL_NOT_VERIFIED", "model": identity})
        elif model.payload["license_id"].lower() in {"unknown", "unverified"} or (
            model.payload["usage"] == "commercial" and "research" in model.payload["license_id"].lower()
        ):
            blockers.append({"code": "LICENSE_NOT_APPROVED", "model": identity})
        else:
            models.append(model)
    doc = db.get(ProjectDocument, plan.payload["document_id"])
    if not doc or doc.sha256 != plan.payload["script_hash"]:
        blockers.append({"code": "SCRIPT_CHANGED"})
    manifest = {
        "plan_id": plan.id,
        "script": plan.payload["script_hash"],
        "links": sorted(links, key=lambda x: x["id"]),
        "style": {"id": style.id, "hash": style.sha256} if style else None,
        "rule": {"id": lock.rulepack_id, "hash": lock.rulepack_hash} if lock else None,
        "workflows": sorted([{"id": w.id, "hash": w.sha256} for w in usable], key=lambda x: x["id"]),
        "models": sorted([{"id": m.id, "hash": m.sha256} for m in models], key=lambda x: x["id"]),
        "offline": offline,
        **({"review_scope": "basic"} if plan.payload.get("review_scope") == "basic" else {}),
        **({"style_input_contract": plan.payload["style_input_contract"]}
           if plan.payload.get("style_input_contract") else {}),
    }
    return {
        "status": "SHOOT_READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "requirements": requirements,
        "manifest": manifest,
        "manifest_hash": graph_hash(manifest),
        "verification": "MOCK_VERIFIED" if offline else "PENDING_LOCAL_GPU",
    }


def _profile_matches_frozen_revision(revision, profile, project_id) -> bool:
    """A project-local profile may use a published revision only by exact identity.

    A published capability revision is project-independent; its validated graph
    and parameter map are the identity. A new project registers its own profile
    from those values, so the historical ``legacy_profile_id`` back-reference
    must not tie production to the original project. The local profile still
    has to reproduce the pinned graph and slot mapping exactly. Media transfer
    declarations stay on the local profile and are validated separately.
    """
    return bool(
        profile
        and (profile.project_id is None or profile.project_id == project_id)
        and graph_hash(revision.payload["graph"]) == profile.graph_sha256
        and revision.payload["category"] == "H3_" + profile.mode.upper()
        and (revision.payload.get("parameter_map") or {}) == (profile.parameter_map or {})
    )


def enforce(db, project_id, *, offline=False, data_dir=None, workflow_profile_id=None, render_request=None):
    plan = current_plan(db, project_id)
    if plan and plan.status != "FROZEN":
        raise HTTPException(409, "PREPRODUCTION_NOT_READY")
    if render_request is not None:
        from shine.quality.input_assessment import assessment_for_request
        from shine.schemas import RenderRequest

        request = RenderRequest.model_validate(render_request)
        if plan and not offline and plan.payload.get("review_scope", "full") != request.self_review_scope:
            raise HTTPException(409, "PREPRODUCTION_REVIEW_SCOPE_MISMATCH")
        if not offline and request.self_review_scope == "basic" and request.shot_spec.characters:
            # The basic character review is an advisory aid. When the frozen
            # plan has no qualified native reviewer, production continues and
            # the character gate stays pending instead of blocking the shot.
            pins = (plan.payload.get("manifest") or {}).get("workflows", []) if plan else []
            rows = [db.get(WorkflowRevision, pin.get("id")) for pin in pins]
            reviewers = [row for row in rows if row and row.payload.get("category") == "VLM_REVIEW"]
            if (len(reviewers) != 1 or reviewers[0].status != "PRODUCTION_APPROVED"
                    or (reviewers[0].payload.get("native_provider") or {}).get("review_scope") != "basic_character"):
                # Advisory-only: the character gate is recorded as pending by
                # the QC step, and the shot is allowed to continue rendering.
                pass

        # New analyzed real projects require independent input evidence. Pinned
        # evidence is rechecked for legacy jobs too; historical data is not rewritten.
        required = bool(plan and not offline and "independent-text-v1" in {
            plan.payload.get("style_input_contract"),
            (plan.payload.get("manifest") or {}).get("style_input_contract"),
        })
        if plan and not offline:
            from shine.comfyops.native import frozen_input_policy

            required = required or frozen_input_policy(db, project_id)
        assessment = assessment_for_request(db, project_id, render_request, offline=offline, required=required)
        if plan and not offline and assessment is not None:
            frozen_input_policy(db, project_id, evidence_present=True)
    if not plan:
        return  # Legacy projects preserve their original contract.
    report = readiness(db, project_id, offline=offline, data_dir=data_dir)
    frozen = plan.payload["manifest"]
    if render_request is None:
        valid = report["status"] == "SHOOT_READY" and graph_hash(frozen) == report["manifest_hash"]
    else:
        # Production uses the exact frozen identities, not whichever newer master now
        # satisfies a project-wide requirement. Revocation blocks only its consumers.
        global_blockers = [b for b in report["blockers"] if "requirement" not in b]
        valid = not global_blockers and all(frozen.get(k) == v for k, v in report["manifest"].items() if k != "links")
    if not valid:
        raise HTTPException(409, "PREPRODUCTION_OUTDATED")
    if workflow_profile_id:
        profile = db.get(WorkflowProfile, workflow_profile_id)
        frozen_ids = {w["id"] for w in plan.payload["manifest"]["workflows"]}
        matched = [
            w
            for w in db.scalars(select(WorkflowRevision))
            if w.id in frozen_ids and _profile_matches_frozen_revision(w, profile, project_id)
        ]
        if not matched:
            raise HTTPException(409, "PREPRODUCTION_WORKFLOW_NOT_FROZEN")
    if render_request is not None:
        enforce_references(db, project_id, render_request, data_dir=data_dir)
    return graph_hash(frozen)


def enforce_references(db, project_id, render_request, *, data_dir=None, expected_manifest_hash=None):
    from shine.models import Shot, ShotAssetBinding
    from shine.schemas import RenderRequest

    plan = current_plan(db, project_id)
    if not plan:
        return
    request = RenderRequest.model_validate(render_request)
    if plan.status != "FROZEN":
        raise HTTPException(409, "PREPRODUCTION_NOT_READY")
    if expected_manifest_hash and graph_hash(plan.payload["manifest"]) != expected_manifest_hash:
        raise HTTPException(409, "PREPRODUCTION_OUTDATED")
    actual = {r.asset_id for r in request.reference_manifest}
    allowed = {}
    for link in plan.payload["manifest"]["links"]:
        asset = db.get(Asset, link["id"])
        voice = db.get(VoiceProfile, link["id"])
        if voice:
            candidate = db.get(CandidateAsset, voice.payload["candidate_id"])
            if not candidate or candidate.formal_asset_id not in actual:
                continue
            if voice.project_id != project_id or voice.status != "FROZEN" or voice.sha256 != link["hash"]:
                raise HTTPException(409, "PREPRODUCTION_VOICE_OUTDATED")
            require_rights(
                db,
                project_id,
                voice.payload["rights_id"],
                "voice_clone" if voice.payload["source"] == "AUTHORIZED_CLONE" else "voice_design",
            )
            candidate = db.get(CandidateAsset, voice.payload["candidate_id"])
            asset = db.get(Asset, candidate.formal_asset_id) if candidate else None
            if not asset or asset.sha256 != voice.payload["conditioning_hash"]:
                raise HTTPException(409, "PREPRODUCTION_VOICE_OUTDATED")
            allowed[asset.id] = asset.sha256
        elif asset:
            if asset.id in actual and link.get("asset_metadata_hash") != asset_metadata_hash(asset):
                raise HTTPException(409, "PREPRODUCTION_ASSET_METADATA_OUTDATED")
            allowed[asset.id] = link["hash"]
        else:
            candidate = db.get(CandidateAsset, link["id"])
            if candidate and candidate.formal_asset_id in actual:
                if (
                    candidate.project_id != project_id
                    or candidate.sha256 != link["hash"]
                    or candidate.status != "PROMOTED"
                ):
                    raise HTTPException(409, "PREPRODUCTION_REFERENCE_OUTDATED")
                allowed[candidate.formal_asset_id] = candidate.payload["content_sha256"]
    shot = db.get(Shot, request.shot_id)
    if not shot or shot.project_id != project_id or shot.revision != request.shot_revision:
        raise HTTPException(409, "PREPRODUCTION_SHOT_OUTDATED")

    def binding_key(row):
        return (row.asset_id, str(row.use_as), row.role, str(row.frame_role) if row.frame_role else None, row.ordinal)

    bound = {binding_key(b) for b in db.scalars(select(ShotAssetBinding).where(ShotAssetBinding.shot_id == shot.id))}
    if (
        bound != {binding_key(r) for r in request.reference_manifest}
        or not set(request.shot_spec.required_asset_ids) <= actual
    ):
        raise HTTPException(409, "PREPRODUCTION_BINDINGS_CHANGED")
    for ref in request.reference_manifest:
        asset = db.get(Asset, ref.asset_id)
        if ref.asset_id not in allowed or allowed[ref.asset_id] != ref.sha256:
            raise HTTPException(409, "PREPRODUCTION_REFERENCE_NOT_FROZEN")
        if (
            not asset
            or asset.project_id != project_id
            or not asset.approved
            or not asset.locked
            or asset.sha256 != ref.sha256
        ):
            raise HTTPException(409, "PREPRODUCTION_REFERENCE_OUTDATED")
        rights_id = (asset.provenance or {}).get("rights_id")
        if rights_id:
            require_rights(db, project_id, rights_id)
        if data_dir:
            from shine.media.artifacts import safe_asset_path

            path = safe_asset_path(data_dir, asset.storage_path)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != ref.sha256:
                raise HTTPException(409, "PREPRODUCTION_REFERENCE_OUTDATED")


def enforce_job(db, job, settings=None):
    plan = current_plan(db, job.project_id)
    offline = job.provider == "mock" or (
        settings.offline_simulation if settings else bool(plan and plan.payload.get("offline"))
    )
    pin = enforce(
        db,
        job.project_id,
        offline=offline,
        data_dir=settings.data_dir if settings else None,
        workflow_profile_id=job.workflow_profile_id if job.provider == "comfyui" else None,
        render_request=job.request,
    )
    if pin != job.preproduction_manifest_hash:
        raise HTTPException(409, "PREPRODUCTION_OUTDATED")
    return pin


def asset_metadata_hash(asset):
    return graph_hash(
        {
            "role": asset.role,
            "entity_id": asset.entity_id,
            "kind": asset.kind,
            "content_type": asset.content_type,
            "duration_s": asset.duration_s,
            "storage_path": asset.storage_path,
        }
    )


#: Capability categories that decide how a produced shot is *reviewed*.  They are
#: not part of what a render is allowed to use, so a new revision of a reviewer
#: is not a new production contract.
REVIEW_CAPABILITY_CATEGORIES = {"VLM_REVIEW", "ASR"}


def _render_bindings(manifest: dict, db) -> dict:
    """The part of a frozen manifest that decides what a render may use.

    The advisory reviewer revisions are deliberately left out: they decide how
    a produced shot is judged afterwards, never what it may be made of.

    The masters are compared by what they *are*, not by the requirement rows
    that the analysis happens to create: every analysis writes new requirement
    rows, so their ids say nothing about the production contract.  A link is
    therefore resolved to the requirement it satisfies and to the master it
    freezes.
    """
    workflows = []
    render_model_ids: set[str] = set()
    models_known = True
    for pin in manifest.get("workflows", []):
        row = db.get(WorkflowRevision, pin.get("id"))
        category = (row.payload.get("category") if row is not None else "") or ""
        if category in REVIEW_CAPABILITY_CATEGORIES:
            continue
        workflows.append({"id": pin.get("id"), "hash": pin.get("hash")})
        if row is None:
            # Without the revision the model set cannot be resolved, so nothing
            # may be filtered out of the declared models.
            models_known = False
            continue
        payload = row.payload
        render_model_ids.update(payload.get("model_ids") or [])
        render_model_ids.update(payload.get("node_package_ids") or [])
    links = []
    for link in manifest.get("links", []):
        requirement = db.get(AssetRequirement, link.get("requirement"))
        payload = requirement.payload if requirement is not None else {}
        asset = db.get(Asset, link.get("id"))
        links.append({
            "family": payload.get("family"),
            "entity_id": payload.get("entity_id"),
            "critical": bool(payload.get("critical")),
            # A voice link names the frozen voice profile, not a picture master.
            "master": asset.id if asset is not None else link.get("id"),
            "hash": link.get("hash"),
            "rights": link.get("rights"),
            "provenance_hash": link.get("provenance_hash"),
            "asset_metadata_hash": link.get("asset_metadata_hash"),
        })
    declared_models = manifest.get("models") or []
    # Only the models a render may use are part of the render contract.  The
    # reviewer revisions bring their own models, and swapping a reviewer model
    # must not look like a changed production contract.
    models = (
        [model for model in declared_models if model.get("id") in render_model_ids]
        if models_known
        else list(declared_models)
    )
    return {
        "script": manifest.get("script"),
        "style": manifest.get("style"),
        "rule": manifest.get("rule"),
        "models": models,
        "links": sorted(links, key=lambda item: (str(item["family"]), str(item["entity_id"]), str(item["master"]))),
        "workflows": sorted(workflows, key=lambda item: str(item["id"])),
        "style_input_contract": manifest.get("style_input_contract"),
    }


def _plan_for_manifest(db, project_id: str, manifest_hash: str):
    """The frozen production plan whose manifest has this exact hash.

    A material-preparation plan carries no H3 manifest, and scanning it here
    would let an asset revision be re-bound as a production contract.
    """
    for plan in db.scalars(
        select(PreProductionPlan).where(
            PreProductionPlan.project_id == project_id,
            PreProductionPlan.purpose == PLAN_PURPOSE_PRODUCTION,
        )
    ).all():
        manifest = plan.payload.get("manifest")
        if manifest and graph_hash(manifest) == manifest_hash:
            return plan
    return None


def revalidate_job_against_current_plan(db, job, *, data_dir=None) -> dict:
    """Re-bind an already produced job to the current frozen production contract.

    A frozen plan revision replaces the contract, and a job that was created
    under the previous revision is no longer bound to it.  Media that was
    already produced under an otherwise identical contract must not be
    invalidated by that, so this re-validates the produced render against the
    current contract and re-binds the job to it.

    The re-validation runs the real production gates:

    * the current plan is frozen and the project is production-ready,
    * the render bindings of the two plan revisions are identical, so the
      Style Bible, the RulePack, the masters, the models and the H3 workflow
      the render used are exactly the ones the current plan still names,
    * the frozen Style Bible of the project is still the one the job used,
    * the RulePack lock still matches the job's request,
    * every reference master is still approved, locked and unchanged, and
    * the shot revision the render used is still the current one.

    Only the reviewer revisions may differ: a new advisory reviewer is a new
    reviewer, not a new production contract.  Nothing here re-renders, and a
    job whose media is not produced yet is never re-bound this way.
    """
    from shine.models import MediaArtifact, Shot
    from shine.schemas import RenderRequest

    if job.stale or job.paused:
        raise HTTPException(409, "PREPRODUCTION_REVALIDATION_JOB_SUPERSEDED")
    current = current_plan(db, job.project_id)
    if current is None or current.status != "FROZEN" or not current.payload.get("manifest"):
        raise HTTPException(409, "PREPRODUCTION_NOT_READY")
    offline = job.provider == "mock" or bool(current.payload.get("offline"))
    if not job.artifact_id:
        raise HTTPException(409, "PREPRODUCTION_REVALIDATION_NEEDS_PRODUCED_MEDIA")
    artifact = db.get(MediaArtifact, job.artifact_id)
    if artifact is None or (not offline and artifact.is_mock):
        raise HTTPException(409, "PREPRODUCTION_REVALIDATION_NEEDS_PRODUCED_MEDIA")
    if not job.preproduction_manifest_hash:
        raise HTTPException(409, "PREPRODUCTION_REVALIDATION_NO_OLD_PLAN")
    previous = _plan_for_manifest(db, job.project_id, job.preproduction_manifest_hash)
    if previous is None or previous.status != "FROZEN":
        raise HTTPException(409, "PREPRODUCTION_REVALIDATION_NO_OLD_PLAN")
    old_bindings = _render_bindings(previous.payload["manifest"], db)
    new_bindings = _render_bindings(current.payload["manifest"], db)
    if old_bindings != new_bindings:
        raise HTTPException(409, "PREPRODUCTION_RENDER_CONTRACT_CHANGED")
    report = readiness(db, job.project_id, offline=offline, data_dir=data_dir)
    if report["status"] != "SHOOT_READY":
        raise HTTPException(409, "PREPRODUCTION_NOT_READY")
    request = RenderRequest.model_validate(job.request)
    style = db.get(StyleVersion, job.style_version_id)
    project = db.get(Project, job.project_id)
    lock = db.get(ProjectRuleLock, job.project_id)
    shot = db.get(Shot, job.shot_id)
    if (
        not style
        or not project
        or project.frozen_style_version_id != style.id
        or style.sha256 != request.style_hash
        or not lock
        or lock.rulepack_id != request.rulepack_id
        or lock.rulepack_hash != request.rulepack_hash
        or not shot
        or shot.revision != job.shot_revision
        or request.shot_revision != job.shot_revision
    ):
        raise HTTPException(409, "PREPRODUCTION_RENDER_CONTRACT_CHANGED")
    enforce_references(db, job.project_id, job.request, data_dir=data_dir)
    previous_hash = job.preproduction_manifest_hash
    new_hash = graph_hash(current.payload["manifest"])
    job.preproduction_manifest_hash = new_hash
    return {
        "previous_manifest_hash": previous_hash,
        "manifest_hash": new_hash,
        "previous_plan_id": previous.id,
        "plan_id": current.id,
        "render_bindings": old_bindings,
    }

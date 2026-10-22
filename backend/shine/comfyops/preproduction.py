from __future__ import annotations

import hashlib
import re

from fastapi import HTTPException
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.assets import FAMILIES
from shine.comfyops.models import (
    AssetRequirement,
    CandidateAsset,
    ModelPackage,
    PreProductionPlan,
    RightsRecord,
    VoiceProfile,
    WorkflowRevision,
)
from shine.models import Asset, Project, ProjectDocument, ProjectRuleLock, StyleVersion


def analyze(db, project, document, extras):
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
    requirements += extras
    unique = {(r["family"], r["entity_id"]): r for r in requirements}
    for r in unique.values():
        if r["family"] not in FAMILIES:
            raise HTTPException(422, "UNKNOWN_ASSET_FAMILY")
    payload = {
        "document_id": document.id,
        "script_hash": document.sha256,
        "requirements": list(unique.values()),
        "manifest": None,
        "offline": True,
    }
    plan = PreProductionPlan(project_id=project.id, payload=payload, sha256=graph_hash(payload), status="ANALYZED")
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
        if r["family"] == "costume" and ("character", r["entity_id"]) in ids:
            deps = [ids[("character", r["entity_id"])]]
        if r["family"] == "dialogue":
            deps = [value for (family, _), value in ids.items() if family == "voice"]
        if r["family"] in {"storyboard", "keyframe"}:
            deps = [value for (family, _), value in ids.items() if family in {"character", "costume", "scene", "prop"}]
        row.payload = {**r, "depends_on": deps, "strategy": "GENERATE_CANDIDATE_OR_IMPORT"}
        row.sha256 = graph_hash(row.payload)
    return plan


def current_plan(db, project_id):
    return db.scalar(
        select(PreProductionPlan)
        .where(PreProductionPlan.project_id == project_id)
        .order_by(PreProductionPlan.created_at.desc(), PreProductionPlan.id.desc())
        .limit(1)
    )


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
        .order_by(VoiceProfile.created_at.desc(), VoiceProfile.id.desc())
    ).all()
    links, blockers, requirements = [], [], []
    for req in db.scalars(select(AssetRequirement).where(AssetRequirement.plan_id == plan.id)).all():
        r = req.payload
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
    workflows = db.scalars(select(WorkflowRevision)).all()
    usable = [w for w in workflows if w.status == ("FAKE_EXECUTED" if offline else "PRODUCTION_APPROVED")]
    for category in ("H3_REF2VA", "VLM_REVIEW", "ASR"):
        if not any(w.payload["category"] == category for w in usable):
            blockers.append({"code": "CAPABILITY_MISSING", "category": category})
    models = db.scalars(select(ModelPackage).where(ModelPackage.status.in_(["MOCK_AVAILABLE", "AVAILABLE"]))).all()
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
    }
    return {
        "status": "SHOOT_READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "requirements": requirements,
        "manifest": manifest,
        "manifest_hash": graph_hash(manifest),
        "verification": "MOCK_VERIFIED" if offline else "PENDING_LOCAL_GPU",
    }


def enforce(db, project_id, *, offline=False, data_dir=None):
    plan = current_plan(db, project_id)
    if not plan:
        return  # Legacy projects preserve their original contract.
    if plan.status != "FROZEN":
        raise HTTPException(409, "PREPRODUCTION_NOT_READY")
    report = readiness(db, project_id, offline=offline, data_dir=data_dir)
    if report["status"] != "SHOOT_READY" or graph_hash(plan.payload["manifest"]) != report["manifest_hash"]:
        raise HTTPException(409, "PREPRODUCTION_OUTDATED")

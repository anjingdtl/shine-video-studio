"""Read-only backup of an explicitly selected existing instance; never adopt or mutate it."""

from datetime import datetime
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import exclusive_file_lock
from shine.comfyops.instances import require_idle, snapshot_tree, tree_manifest
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan
from shine.comfyops.scope import scoped_settings
from shine.models import utcnow
from shine.services import add_audit, require_project


def plan_backup(db, settings, project_id, instance_id, root, mode, risk_confirmed):
    require_project(db, project_id)
    instance = db.get(ComfyInstance, instance_id)
    selected = scoped_settings(db, settings)
    source = Path(root).resolve()
    if not instance or instance.payload.get("managed"):
        raise HTTPException(409, "EXISTING_EXTERNAL_INSTANCE_REQUIRED")
    if source not in [Path(p).resolve() for p in selected.comfy_probe_roots]:
        raise HTTPException(403, "BACKUP_ROOT_NOT_EXPLICITLY_ALLOWLISTED")
    if bool(instance.payload.get("is_mock")) != (mode == "fake") or not risk_confirmed:
        raise HTTPException(409, "BACKUP_MODE_OR_RISK_CONFIRMATION_REQUIRED")
    from urllib.parse import urlparse

    port = urlparse(instance.payload["url"]).port
    if port not in selected.comfy_probe_ports:
        raise HTTPException(403, "INSTANCE_PORT_OUTSIDE_SCOPE")
    payload = {
        "kind": "INSTANCE_BACKUP",
        "instance_id": instance_id,
        "instance_hash": instance.sha256,
        "source_root": str(source),
        "source_tree": tree_manifest(source),
        "port": port,
        "is_mock": mode == "fake",
        "tier": "L3",
        "commands": [],
        "packages": [],
        "isolated": True,
        "executor": "FAKE_ONLY" if mode == "fake" else "LOCAL_ISOLATED",
    }
    row = InstallationPlan(
        project_id=project_id, payload=payload, sha256=graph_hash(payload), status="AWAITING_APPROVAL"
    )
    db.add(row)
    db.flush()
    return row


def execute_backup(factory, settings, identity, digest, actor, http):
    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        plan = db.get(InstallationPlan, identity)
        if not plan or plan.status != "APPROVED" or plan.sha256 != digest or graph_hash(plan.payload) != digest:
            raise HTTPException(409, "BACKUP_APPROVAL_OR_HASH_INVALID")
        p = plan.payload
        if p.get("kind") != "INSTANCE_BACKUP" or p.get("approved_by") != actor:
            raise HTTPException(403, "BACKUP_NOT_AUTHORIZED")
        if datetime.fromisoformat(p["execute_before"]) <= utcnow():
            raise HTTPException(409, "BACKUP_APPROVAL_EXPIRED")
        instance = db.get(ComfyInstance, p["instance_id"])
        selected = scoped_settings(db, settings)
        if (
            not instance
            or instance.sha256 != p["instance_hash"]
            or p["source_root"] not in [str(Path(x).resolve()) for x in selected.comfy_probe_roots]
            or p["port"] not in selected.comfy_probe_ports
        ):
            raise HTTPException(409, "BACKUP_SCOPE_OR_INSTANCE_CHANGED")
        attempt = InstallationAttempt(
            plan_id=identity,
            payload={
                "instance_id": instance.id,
                "kind": "INSTANCE_BACKUP",
                "source_root": p["source_root"],
                "is_mock": p["is_mock"],
            },
            sha256=digest,
            status="SUBMIT_INTENT",
        )
        db.add(attempt)
        db.flush()
        aid = attempt.id
        plan.status = "INSTALLING"
    target = settings.comfyops_dir / "external-backups" / aid
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive_file_lock(target.with_suffix(".lock")):
            require_idle(p["port"], http)
            source = Path(p["source_root"])
            if tree_manifest(source) != p["source_tree"]:
                raise ValueError("BACKUP_SOURCE_CHANGED")
            target.parent.mkdir(parents=True, exist_ok=True)
            manifest = snapshot_tree(source, target)
            if manifest != p["source_tree"] or tree_manifest(source) != manifest:
                raise ValueError("BACKUP_SOURCE_CHANGED")
            require_idle(p["port"], http)
    except Exception:
        with factory.begin() as db:
            db.get(InstallationAttempt, aid).status = "NEEDS_REVIEW"
            db.get(InstallationPlan, identity).status = "NEEDS_REVIEW"
        raise
    with factory.begin() as db:
        attempt = db.get(InstallationAttempt, aid)
        attempt.status = "SNAPSHOT"
        attempt.payload = {**attempt.payload, "snapshot_path": str(target), "files": manifest, "source_unchanged": True}
        attempt.sha256 = graph_hash(attempt.payload)
        db.get(InstallationPlan, identity).status = "SNAPSHOT"
        add_audit(
            db,
            actor,
            "EXTERNAL_INSTANCE_BACKED_UP_READ_ONLY",
            "installation_attempt",
            aid,
            {"source_unchanged": True, "is_mock": p["is_mock"]},
            project_id=plan.project_id,
        )
    return aid

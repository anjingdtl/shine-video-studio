"""Windows-only opt-in client for a specifically approved isolated install.

Run locally: python -m shine.comfyops.local PLAN_ID PLAN_SHA256 --execute
No server route silently enables it. Credentials are entered by the human on
stdin; no credential is accepted from a model, CLI argument, or plan payload.
"""

from __future__ import annotations

import argparse
import getpass
import platform
import shutil
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import (
    ControlledDownloader,
    HTTPTransport,
    LocalInstaller,
    PackageSpec,
    exclusive_file_lock,
    safe_target,
)
from shine.comfyops.models import InstallationAttempt, InstallationPlan, ModelPackage, NodePackage
from shine.config import Settings
from shine.db import make_engine, make_session_factory
from shine.models import utcnow
from shine.operator_review import authenticate
from shine.services import add_audit


def reconcile_failed_install(db, settings, attempt, actor, decision):
    """Inspect terminal failed packages under their OS locks; never retry or delete.

    Unknown journals/partial destinations remain blocked. A human grant records
    the observation, then a separate fresh plan is needed for any installation.
    """
    from shine.comfyops import approval

    plan = db.get(InstallationPlan, attempt.plan_id)
    if attempt.status != "FAILED_RECONCILE_REQUIRED" or not plan or plan.status != "NEEDS_REVIEW":
        raise ValueError("FAILED_INSTALL_ATTEMPT_REQUIRED")
    if plan.payload.get("kind") or plan.payload.get("executor") != "LOCAL_ISOLATED" or not plan.payload.get("isolated"):
        raise ValueError("ISOLATED_PACKAGE_PLAN_REQUIRED")
    if graph_hash(plan.payload) != plan.sha256 or attempt.payload.get("plan_hash") != plan.sha256:
        raise ValueError("PLAN_HASH_CHANGED")
    if db.scalar(select(InstallationAttempt.id).where(InstallationAttempt.status == "SUBMIT_INTENT").limit(1)):
        raise ValueError("INSTALL_ATTEMPT_ACTIVE")
    root = settings.data_dir / "comfyops" / "isolated-packages"
    if not root.resolve().is_relative_to(settings.data_dir.resolve()):
        raise ValueError("INSTALL_ROOT_OUTSIDE_SCOPE")
    observations = []
    with ExitStack() as locks:
        for package in sorted(plan.payload["packages"], key=lambda p: p["spec"]["name"]):
            spec = PackageSpec.model_validate(package["spec"])
            cls = ModelPackage if spec.kind == "model" else NodePackage
            registered = db.get(cls, package["id"])
            if (
                not registered or registered.sha256 != package["hash"]
                or graph_hash(registered.payload) != package["hash"]
                or graph_hash(package["spec"]) != package["hash"]
            ):
                raise ValueError("PLAN_HASH_CHANGED")
            if root.exists():
                locks.enter_context(exclusive_file_lock(safe_target(root, spec.name + ".lock")))
            for suffix in (".journal", ".staging", ".rollback"):
                if safe_target(root, spec.name + suffix).exists():
                    raise ValueError("INSTALL_FILESYSTEM_RECOVERY_REQUIRED")
            destination = safe_target(root, spec.name)
            status = "ABSENT"
            if destination.exists():
                expected = {f.relative_path.replace("\\", "/").casefold() for f in spec.files}
                actual = {
                    p.relative_to(destination).as_posix().casefold()
                    for p in destination.rglob("*") if p.is_file()
                }
                if actual != expected or not all(
                    ControlledDownloader.valid(safe_target(destination, f.relative_path), f) for f in spec.files
                ):
                    raise ValueError("INSTALL_DESTINATION_CHANGED")
                status = "MATCHES_PINNED_FILES"
            observations.append({"package_id": package["id"], "package_sha256": package["hash"], "files": status})
        approval.consume(db, actor, "install_reconcile", attempt, decision.grant_id, decision.snapshot)
        attempt.status = "RECONCILED_NO_RETRY"
        attempt.payload = {**attempt.payload, "reconciliation": {
            "actor": actor, "at": str(utcnow()), "evidence": decision.evidence, "packages": observations,
            "files_modified": False, "runtime_verified": False, "resubmitted": False,
        }}
        attempt.sha256 = graph_hash(attempt.payload)
        plan.status = "RECONCILED_NO_RETRY"
        add_audit(db, actor, "LOCAL_INSTALL_FAILURE_RECONCILED", "installation_attempt", attempt.id,
                  attempt.payload["reconciliation"], project_id=plan.project_id)
        db.commit()
    return attempt


def execute_approved(factory, settings, plan_id, plan_hash, actor, installer):
    with factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        plan = db.get(InstallationPlan, plan_id)
        if (
            not plan
            or plan.status != "APPROVED"
            or plan.sha256 != plan_hash
            or graph_hash(plan.payload) != plan_hash
            or plan.payload.get("approved_by") != actor
        ):
            raise ValueError("INSTALL_APPROVAL_REQUIRED_OR_PLAN_HASH_CHANGED")
        if plan.payload.get("kind") in {"INSTANCE_LIFECYCLE", "INSTANCE_BACKUP"}:
            raise ValueError("USE_INSTANCE_LIFECYCLE_OR_BACKUP_EXECUTOR")
        if datetime.fromisoformat(plan.payload.get("execute_before", "2000-01-01")) <= utcnow():
            raise ValueError("INSTALL_APPROVAL_EXPIRED")
        if not plan.payload.get("isolated") or plan.payload.get("executor") != "LOCAL_ISOLATED":
            raise ValueError("LOCAL_PLAN_REQUIRED")
        for package in plan.payload["packages"]:
            cls = ModelPackage if package["spec"]["kind"] == "model" else NodePackage
            row = db.get(cls, package["id"])
            if (
                not row or row.sha256 != package["hash"]
                or graph_hash(row.payload) != package["hash"] or graph_hash(package["spec"]) != package["hash"]
            ):
                raise ValueError("PLAN_HASH_CHANGED")
        if shutil.disk_usage(settings.data_dir).free < plan.payload.get("disk_required", 0):
            raise ValueError("INSUFFICIENT_DISK")
        plan.status = "INSTALLING"
        attempt = InstallationAttempt(
            plan_id=plan.id,
            payload={"plan_hash": plan_hash, "started_at": str(utcnow()), "system_scope": "NEW_ISOLATED_PACKAGES_ONLY"},
            sha256=plan_hash,
            status="SUBMIT_INTENT",
        )
        db.add(attempt)
        db.flush()
        attempt_id = attempt.id
        packages = list(plan.payload["packages"])
        add_audit(
            db,
            actor,
            "LOCAL_INSTALL_INTENT",
            "installation_plan",
            plan.id,
            {"hash": plan_hash},
            project_id=plan.project_id,
        )
        db.commit()
    results = []
    try:
        for package in packages:
            results.append(installer.install(PackageSpec.model_validate(package["spec"])))
    except Exception as exc:
        # Never auto retry after a process interruption; the persisted attempt requires reconciliation.
        with factory.begin() as db:
            db.get(InstallationPlan, plan_id).status = "NEEDS_REVIEW"
            failed = db.get(InstallationAttempt, attempt_id)
            failed.status = "FAILED_RECONCILE_REQUIRED"
            failed.payload = {**failed.payload, "completed_packages": len(results), "error_type": type(exc).__name__}
            add_audit(
                db,
                actor,
                "LOCAL_INSTALL_FAILED",
                "installation_plan",
                plan_id,
                {"error_type": type(exc).__name__},
                project_id=db.get(InstallationPlan, plan_id).project_id,
            )
        raise
    with factory.begin() as db:
        plan = db.get(InstallationPlan, plan_id)
        for package in packages:
            cls = ModelPackage if package["spec"]["kind"] == "model" else NodePackage
            # Files verified != working model/node. Local inference/registration still required.
            db.get(cls, package["id"]).status = "FILES_VERIFIED_PENDING_RUNTIME"
        plan.status = "FILES_VERIFIED_PENDING_RUNTIME"
        attempt = db.get(InstallationAttempt, attempt_id)
        attempt.status = "FILES_VERIFIED"
        attempt.payload = {**attempt.payload, "results": results, "runtime_verified": False}
        attempt.sha256 = graph_hash(attempt.payload)
        add_audit(
            db,
            actor,
            "LOCAL_PACKAGE_FILES_VERIFIED",
            "installation_plan",
            plan_id,
            {"attempt_id": attempt.id},
            project_id=plan.project_id,
        )
    return attempt_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_id")
    parser.add_argument("plan_sha256")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or platform.system() != "Windows":
        parser.error("Explicit --execute on the authorized Windows host is required")
    settings = Settings.from_env()
    actor = authenticate(settings, getpass.getpass("Local human operator credential: "))
    factory = make_session_factory(make_engine(settings.database_url))
    root: Path = settings.data_dir / "comfyops" / "isolated-packages"
    installer = LocalInstaller(
        root, ControlledDownloader({"huggingface.co", "raw.githubusercontent.com", "github.com"}, HTTPTransport())
    )
    identity = execute_approved(factory, settings, args.plan_id, args.plan_sha256, actor, installer)
    print(f"FILES_VERIFIED; attempt={identity}; runtime and GPU validation remain pending")


if __name__ == "__main__":
    main()

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
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import ControlledDownloader, HTTPTransport, LocalInstaller, PackageSpec
from shine.comfyops.models import InstallationAttempt, InstallationPlan, ModelPackage, NodePackage
from shine.config import Settings
from shine.db import make_engine, make_session_factory
from shine.models import utcnow
from shine.operator_review import authenticate
from shine.services import add_audit


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
        if plan.payload.get("kind") == "INSTANCE_LIFECYCLE":
            raise ValueError("USE_INSTANCE_LIFECYCLE_EXECUTOR")
        if datetime.fromisoformat(plan.payload.get("execute_before", "2000-01-01")) <= utcnow():
            raise ValueError("INSTALL_APPROVAL_EXPIRED")
        if not plan.payload.get("isolated") or plan.payload.get("executor") != "LOCAL_ISOLATED":
            raise ValueError("LOCAL_PLAN_REQUIRED")
        for package in plan.payload["packages"]:
            cls = ModelPackage if package["spec"]["kind"] == "model" else NodePackage
            row = db.get(cls, package["id"])
            if not row or row.sha256 != package["hash"]:
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

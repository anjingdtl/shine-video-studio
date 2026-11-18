"""Windows-only opt-in client for a specifically approved isolated install.

Run locally: python -m shine.comfyops.local PLAN_ID PLAN_SHA256 --execute
No server route silently enables it. Credentials are entered by the human on
stdin; no credential is accepted from a model, CLI argument, or plan payload.
"""

from __future__ import annotations

import argparse
import getpass
import platform
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text

from shine.comfy.workflow import graph_hash
from shine.comfyops.installer import (
    TRUSTED_DOWNLOAD_HOSTS,
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


def advance_verified_package(db, registered):
    """Promote a package whose complete pinned file set verified in place.

    ``FILES_VERIFIED_PENDING_RUNTIME`` states the proven fact - every file of
    the package is on disk and hash-matches the pinned upstream digest. It is
    not a runtime claim: loading the checkpoint is still separate work.

    A package whose files are absent, partial or changed keeps its current
    status; only a full set of matching files advances it.
    """
    if registered.status == "FILES_VERIFIED_PENDING_RUNTIME":
        return False
    if registered.status != "UNVERIFIED_ORIGIN":
        return False
    registered.status = "FILES_VERIFIED_PENDING_RUNTIME"
    return True


def reconcile_failed_install(db, settings, attempt, actor, decision):
    """Inspect terminal failed packages under their OS locks; never retry or delete.

    Unknown journals/partial destinations remain blocked. A human grant records
    the observation, then a separate fresh plan is needed for any installation.

    A package whose complete pinned file set is present and hash-matching is
    advanced to ``FILES_VERIFIED_PENDING_RUNTIME``: the bytes were really
    verified against the pinned digests, so the registry must state that. A
    package whose files are absent keeps its status and still needs a fresh
    plan.

    Reconciling an already reconciled attempt re-observes the same directories
    (no network, no deletion) so a package that completed on disk after the
    first observation can still advance.
    """
    from shine.comfyops import approval

    plan = db.get(InstallationPlan, attempt.plan_id)
    reverify = attempt.status == "RECONCILED_NO_RETRY"
    if not plan or (
        not reverify and attempt.status not in {"FAILED_RECONCILE_REQUIRED", "SUBMIT_INTENT"}
    ):
        raise ValueError("FAILED_INSTALL_ATTEMPT_REQUIRED")
    if reverify:
        if plan.status != "RECONCILED_NO_RETRY":
            raise ValueError("FAILED_INSTALL_ATTEMPT_REQUIRED")
    elif plan.status not in {"NEEDS_REVIEW", "INSTALLING"}:
        raise ValueError("FAILED_INSTALL_ATTEMPT_REQUIRED")
    if plan.payload.get("kind") or plan.payload.get("executor") != "LOCAL_ISOLATED" or not plan.payload.get("isolated"):
        raise ValueError("ISOLATED_PACKAGE_PLAN_REQUIRED")
    if graph_hash(plan.payload) != plan.sha256 or attempt.payload.get("plan_hash") != plan.sha256:
        raise ValueError("PLAN_HASH_CHANGED")
    # The attempt being reconciled is expected to look active when the owning
    # process died mid-install; only some *other* active install blocks it.
    if db.scalar(select(InstallationAttempt.id).where(
            InstallationAttempt.status == "SUBMIT_INTENT", InstallationAttempt.id != attempt.id).limit(1)):
        raise ValueError("INSTALL_ATTEMPT_ACTIVE")
    root = settings.comfyops_dir / "isolated-packages"
    if not root.resolve().is_relative_to(settings.comfyops_dir.resolve()):
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
                # A killed download leaves a journal/staging behind. Recovery is
                # deterministic and verified; it never retries a network call.
                # It takes (and releases) the same exclusive lock itself, so the
                # lock is re-acquired afterwards for the observation below.
                LocalInstaller(root, None).recover(spec.name)
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
            advanced = advance_verified_package(db, registered) if status == "MATCHES_PINNED_FILES" else False
            if status == "MATCHES_PINNED_FILES":
                from shine.comfyops.verification_receipts import save_receipt

                plan_variant = (plan.payload or {}).get("variant") or {}
                deployment = (plan.payload or {}).get("deployment") or {}
                save_receipt(
                    db,
                    variant_id=plan_variant.get("variant_id"),
                    catalog_id=deployment.get("catalog_id"),
                    catalog_version=deployment.get("catalog_version"),
                    component={
                        "component_id": spec.name,
                        "revision": spec.revision,
                        "files": [file.model_dump() for file in spec.files],
                    },
                    root=root,
                    directory=destination,
                )
            observations.append({
                "package_id": package["id"],
                "package_sha256": package["hash"],
                "files": status,
                "registry_status": registered.status,
                "registry_advanced": advanced,
            })
        approval.consume(db, actor, "install_reconcile", attempt, decision.grant_id, decision.snapshot)
        previous_status = attempt.status
        attempt.status = "RECONCILED_NO_RETRY"
        # One terminal fact, written once: a reconciled attempt must not keep a
        # stage that still says "awaiting review" while its status is terminal.
        attempt.stage = "RECONCILED_NO_RETRY"
        advanced = [row["package_id"] for row in observations if row["registry_advanced"]]
        attempt.payload = {**attempt.payload, "reconciliation": {
            "actor": actor, "at": str(utcnow()), "evidence": decision.evidence, "packages": observations,
            "previous_status": previous_status, "files_modified": False, "runtime_verified": False,
            "resubmitted": False, "packages_advanced": advanced,
        }}
        attempt.sha256 = graph_hash(attempt.payload)
        plan.status = "RECONCILED_NO_RETRY"
        add_audit(db, actor, "LOCAL_INSTALL_FAILURE_RECONCILED", "installation_attempt", attempt.id,
                  attempt.payload["reconciliation"], project_id=plan.project_id)
        if advanced:
            add_audit(db, actor, "VERIFIED_PACKAGES_ADVANCED", "installation_attempt", attempt.id,
                      {"packages": advanced,
                       "status": "FILES_VERIFIED_PENDING_RUNTIME",
                       "evidence": "pinned file set hash-verified in place; runtime not yet proven"},
                      project_id=plan.project_id)
        db.commit()
    return attempt


_PRE_START_INSTALL_FAILURE_CODES = frozenset({
    "INSTALL_PLAN_MISSING",
    "PLAN_HASH_CHANGED",
    "LOCAL_PLAN_REQUIRED",
})


def reconcile_stopped_isolated_attempts(
    db,
    settings,
    *,
    actor: str,
    evidence: str = "setup-reconcile",
) -> list[dict]:
    """Reconcile stopped failed/abandoned isolated package attempts under the single setup executor.

    ``SetupPanel``'s legacy manual button was retired when ``SetupCenter`` became
    the single preparation entrypoint.  Without closing stopped
    ``FAILED_RECONCILE_REQUIRED`` (or provably dead ``SUBMIT_INTENT``) package
    attempts during ``RECONCILE``, ``require_idle_configuration`` blocks ``RECIPE``
    forever with no live process running.  Every filesystem check here uses the
    same OS lock and ``LocalInstaller.recover`` contract as ``reconcile_failed_install``;
    any tampered or unrecoverable destination stays ``FAILED_RECONCILE_REQUIRED``.
    """
    from shine.comfyops.installer import process_identity
    from shine.comfyops.verification_receipts import save_receipt

    root = settings.comfyops_dir / "isolated-packages"
    if not root.resolve().is_relative_to(settings.comfyops_dir.resolve()):
        return []

    candidates = db.scalars(
        select(InstallationAttempt).where(
            InstallationAttempt.status.in_(["FAILED_RECONCILE_REQUIRED", "SUBMIT_INTENT"]),
            InstallationAttempt.lease_owner.is_(None),
        )
    ).all()
    if not candidates:
        return []

    reconciled: list[dict] = []
    for attempt in candidates:
        payload = attempt.payload if isinstance(attempt.payload, dict) else {}
        if attempt.status == "SUBMIT_INTENT":
            owner_pid = payload.get("owner_pid")
            owner_birth = payload.get("owner_birth")
            if type(owner_pid) is not int or owner_pid <= 0 or not isinstance(owner_birth, str) or not owner_birth:
                continue
            try:
                current_birth = process_identity(owner_pid)
            except (OSError, ValueError):
                continue  # unreadable identity is uncertainty, never death
            if current_birth == owner_birth:
                continue
            if db.scalar(
                select(InstallationAttempt.id).where(
                    InstallationAttempt.status == "SUBMIT_INTENT",
                    InstallationAttempt.id != attempt.id,
                ).limit(1)
            ):
                continue

        plan = db.get(InstallationPlan, attempt.plan_id) if attempt.plan_id else None
        plan_payload = (plan.payload if plan is not None and isinstance(plan.payload, dict) else None) or {}
        # The same hard admission as reconcile_failed_install: a missing plan,
        # a non-isolated plan, or a digest that no longer matches the record is
        # a corrupt ledger row, not a stopped installation to close silently.
        if plan is None:
            continue
        if (plan_payload.get("kind")
                or plan_payload.get("executor") != "LOCAL_ISOLATED"
                or not plan_payload.get("isolated")):
            continue
        if graph_hash(plan_payload) != plan.sha256 or attempt.payload.get("plan_hash") != plan.sha256:
            continue
        packages = plan_payload.get("packages") or []
        if not isinstance(packages, list) or not packages:
            continue
        # A package entry without its spec is a damaged record; it must block
        # the reconciliation instead of being skipped as "not interesting".
        if any(not isinstance(p, dict) or not isinstance(p.get("spec"), dict) for p in packages):
            continue

        error_code = str(attempt.error_code or "")
        error_message = str(attempt.error_message or "")
        pre_start_refusal = (
            error_code in _PRE_START_INSTALL_FAILURE_CODES
            or (
                error_code in {"RESOURCE_DOWNLOAD_CHANGED", "RESOURCE_PLAN_REVALIDATION_FAILED"}
                and ("no download was started" in error_message.lower()
                     or plan.status == "APPROVED")
            )
        )
        if not pre_start_refusal and plan.status not in {"NEEDS_REVIEW", "INSTALLING"}:
            continue

        observations: list[dict] = []
        savepoint = db.begin_nested()
        try:
            with ExitStack() as locks:
                for package in sorted(packages, key=lambda p: str((p.get("spec") or {}).get("name") or "")):
                    spec = PackageSpec.model_validate(package["spec"])
                    if root.exists():
                        LocalInstaller(root, None).recover(spec.name)
                        locks.enter_context(exclusive_file_lock(safe_target(root, spec.name + ".lock")))
                    for suffix in (".journal", ".staging", ".rollback"):
                        if safe_target(root, spec.name + suffix).exists():
                            raise ValueError("INSTALL_FILESYSTEM_RECOVERY_REQUIRED")
                    destination = safe_target(root, spec.name)
                    file_status = "ABSENT"
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
                        file_status = "MATCHES_PINNED_FILES"

                    cls = ModelPackage if spec.kind == "model" else NodePackage
                    registered = db.get(cls, package.get("id"))
                    hash_matches = bool(
                        registered is not None
                        and registered.sha256 == package.get("hash")
                        and graph_hash(registered.payload) == package.get("hash")
                        and graph_hash(package["spec"]) == package.get("hash")
                    )
                    advanced = (
                        advance_verified_package(db, registered)
                        if file_status == "MATCHES_PINNED_FILES" and hash_matches
                        else False
                    )
                    if file_status == "MATCHES_PINNED_FILES" and hash_matches:
                        plan_variant = plan_payload.get("variant") or {}
                        plan_deployment = plan_payload.get("deployment") or {}
                        save_receipt(
                            db,
                            variant_id=plan_variant.get("variant_id"),
                            catalog_id=plan_deployment.get("catalog_id"),
                            catalog_version=plan_deployment.get("catalog_version"),
                            component={
                                "component_id": spec.name,
                                "revision": spec.revision,
                                "files": [file.model_dump() for file in spec.files],
                            },
                            root=root,
                            directory=destination,
                        )
                    observations.append({
                        "package_id": package.get("id"),
                        "package_sha256": package.get("hash"),
                        "files": file_status,
                        "registry_status": registered.status if registered is not None else None,
                        "registry_advanced": advanced,
                    })
        except ValueError:
            # Roll the savepoint back: a package that advanced earlier in this
            # loop must not leave a partial promotion behind when a later
            # package proves the record tampered or unrecoverable.
            savepoint.rollback()
            continue
        else:
            savepoint.commit()

        previous_status = attempt.status
        attempt.status = "RECONCILED_NO_RETRY"
        attempt.stage = "RECONCILED_NO_RETRY"
        advanced_ids = [
            row["package_id"]
            for row in observations
            if row.get("registry_advanced") and row.get("package_id")
        ]
        attempt.payload = {
            **payload,
            "reconciliation": {
                "actor": actor,
                "at": str(utcnow()),
                "evidence": evidence,
                "packages": observations,
                "previous_status": previous_status,
                "files_modified": False,
                "runtime_verified": False,
                "resubmitted": False,
                "packages_advanced": advanced_ids,
            },
        }
        attempt.sha256 = graph_hash(attempt.payload)
        if plan is not None and plan.status in {"APPROVED", "NEEDS_REVIEW", "INSTALLING"}:
            plan.status = "RECONCILED_NO_RETRY"
        project_id = plan.project_id if plan is not None else None
        add_audit(
            db, actor, "LOCAL_INSTALL_FAILURE_RECONCILED", "installation_attempt", attempt.id,
            attempt.payload["reconciliation"], project_id=project_id,
        )
        if advanced_ids:
            add_audit(
                db, actor, "VERIFIED_PACKAGES_ADVANCED", "installation_attempt", attempt.id,
                {
                    "packages": advanced_ids,
                    "status": "FILES_VERIFIED_PENDING_RUNTIME",
                    "evidence": "pinned file set hash-verified in place; runtime not yet proven",
                },
                project_id=project_id,
            )
        reconciled.append(
            {
                "attempt_id": attempt.id,
                "previous_status": previous_status,
                "packages_advanced": advanced_ids,
            }
        )
    if reconciled:
        db.flush()
    return reconciled


def validate_approved_plan(db, settings, plan_id, plan_hash, actor, executor="LOCAL_ISOLATED"):
    """Every check an approved plan must pass before an attempt is queued.

    The approval hash, the single-use grant and the pinned package digests are
    re-verified here, so an attempt may never execute content that the human
    did not approve.
    """
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
    if plan.payload.get("executor") != executor:
        raise ValueError("LOCAL_PLAN_REQUIRED" if executor == "LOCAL_ISOLATED" else "FAKE_EXECUTOR_REQUIRED")
    if executor == "LOCAL_ISOLATED" and not plan.payload.get("isolated"):
        raise ValueError("LOCAL_PLAN_REQUIRED")
    for package in plan.payload["packages"]:
        cls = ModelPackage if package["spec"]["kind"] == "model" else NodePackage
        row = db.get(cls, package["id"])
        if (
            not row or row.sha256 != package["hash"]
            or graph_hash(row.payload) != package["hash"] or graph_hash(package["spec"]) != package["hash"]
        ):
            raise ValueError("PLAN_HASH_CHANGED")
    if executor == "LOCAL_ISOLATED":
        from shine.comfyops.resource_resolver import reject_unbound_candidate_checkpoint
        from shine.comfyops.storage import disk_info, require_storage

        # Keep the SQLite writer transaction short; the Worker revalidates
        # the full resource identity outside this transaction before transfer.
        reject_unbound_candidate_checkpoint(plan.payload)
        selected_root = require_storage(settings)
        if plan.payload.get('storage_root', str(selected_root)) != str(selected_root):
            raise ValueError('DEPLOYMENT_STORAGE_CHANGED_RECREATE_PLAN')
        if disk_info(selected_root)['free_bytes'] < plan.payload.get("disk_required", 0):
            raise ValueError("INSUFFICIENT_DISK")
    return plan


def queue_install_attempt(factory, settings, plan_id, plan_hash, actor, executor="LOCAL_ISOLATED",
                          *, claim_guard=None) -> tuple[str, bool]:
    """Queue the persistent attempt.  Nothing is downloaded in this request.

    The Worker claims the attempt afterwards, writes its stage and its
    confirmed bytes, and the browser re-attaches by the returned attempt id.
    """
    from shine.comfyops.installation import queue_attempt

    with factory.begin() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if claim_guard is not None:
            claim_guard(db)
        plan = validate_approved_plan(db, settings, plan_id, plan_hash, actor, executor)
        attempt, reused = queue_attempt(db, plan, plan_hash, actor)
        attempt_id = attempt.id
    return attempt_id, reused


def execute_approved(factory, settings, plan_id, plan_hash, actor, installer):
    with factory() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        plan = validate_approved_plan(db, settings, plan_id, plan_hash, actor)
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
    root: Path = settings.comfyops_dir / "isolated-packages"
    installer = LocalInstaller(
        root, ControlledDownloader(TRUSTED_DOWNLOAD_HOSTS, HTTPTransport())
    )
    identity = execute_approved(factory, settings, args.plan_id, args.plan_sha256, actor, installer)
    print(f"FILES_VERIFIED; attempt={identity}; runtime and GPU validation remain pending")


if __name__ == "__main__":
    main()

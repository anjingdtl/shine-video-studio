"""Evidence-bound adoption of an already installed local ComfyUI.

Adoption records the existing installation in Shine's durable ledger. It never
copies, downloads, patches, or writes files into the user's ComfyUI tree.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from collections import Counter
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.existing_installation import comfy_root, inspect, local_path
from shine.comfyops.instances import instance_identity
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan
from shine.models import AuditEvent
from shine.services import add_audit, require_project

_CODE_SUFFIXES = {".py", ".pyi", ".pyd", ".dll", ".json", ".toml", ".yaml", ".yml", ".ini"}
_IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
# These are the additional folders exposed by the pinned ComfyUI revision's
# folder_paths aliases. Reuse stays in place; no model is relocated.
_MODEL_FAMILY_ALIASES = {
    "text_encoders": ("clip",),
    "diffusion_models": ("unet",),
}


def _node_tree_fingerprint(directory: Path) -> str:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("EXISTING_NODE_DIRECTORY_INVALID")
    root = directory.resolve()
    rows = []
    for path in sorted(directory.rglob("*")):
        if any(part in _IGNORED_PARTS for part in path.relative_to(directory).parts):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("EXISTING_NODE_SYMLINK_FORBIDDEN")
        if path.is_file() and path.suffix.lower() in _CODE_SUFFIXES:
            if len(rows) >= 20000:
                raise ValueError("EXISTING_NODE_FILE_LIMIT")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            rows.append({"path": path.relative_to(directory).as_posix(), "sha256": digest})
    if not rows:
        raise ValueError("EXISTING_NODE_CODE_MISSING")
    return graph_hash(rows)


def _node_fingerprints(root: Path, catalog: dict) -> dict[str, str]:
    result = {}
    for component in catalog.get("components", []):
        if component.get("kind") != "node":
            continue
        candidate = root / "custom_nodes" / component["component_id"]
        if candidate.exists():
            result[component["component_id"]] = _node_tree_fingerprint(candidate)
    sampling_node = root / "custom_nodes" / "shine_h3_sampling_mode.py"
    if sampling_node.is_file() and not sampling_node.is_symlink():
        result["shine_h3_sampling_mode.py"] = hashlib.sha256(sampling_node.read_bytes()).hexdigest()
    return result


def _source_revision(root: Path) -> str | None:
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _verified_existing_files(db, report: dict) -> list[dict]:
    audits = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "EXISTING_COMFY_INSTALLATION_VERIFIED",
            AuditEvent.entity_type == "deployment",
            AuditEvent.entity_id == report["catalog_id"],
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(100)
    ).all()
    for event in audits:
        evidence = event.payload or {}
        if evidence.get("snapshot") != report["snapshot"] or evidence.get("root") not in {
            report["root"], "[local-path]"
        }:
            continue
        if "verified_files" in evidence:
            return list(evidence.get("verified_files") or [])

        # Older audit records stored per-component counts instead of per-file
        # evidence. Reconstruct against this exact immutable scan snapshot,
        # matching the existing verifier's cache-recovery contract.
        verified_counts = Counter(evidence.get("verified_components") or [])
        rejected_counts = Counter(evidence.get("rejected_components") or [])
        if set(verified_counts) & set(rejected_counts):
            continue
        verified, rejected = [], []
        for match in report.get("matches", []):
            component = match["component_id"]
            if verified_counts[component] > 0:
                verified.append({key: match[key] for key in (
                    "component_id", "relative_path", "sha256", "size"
                )})
                verified_counts[component] -= 1
            elif rejected_counts[component] > 0:
                rejected.append(match)
                rejected_counts[component] -= 1
            else:
                break
        if (len(verified) + len(rejected) != len(report.get("matches", []))
                or any(verified_counts.values()) or any(rejected_counts.values())
                or sum(item["size"] for item in verified) != evidence.get("verified_bytes")):
            continue
        if rejected:
            raise HTTPException(409, "EXISTING_MODEL_HASH_REJECTIONS_PRESENT")
        return verified
    raise HTTPException(409, "EXISTING_INSTALL_HASH_VERIFICATION_REQUIRED")


def _required_h3_files(catalog: dict) -> set[tuple[str, str, str, int]]:
    return {
        (component["component_id"], item["relative_path"], item["sha256"], item["size"])
        for component in catalog.get("components", [])
        if component.get("kind") == "model" and component["component_id"].startswith("minimax-h3-")
        for item in component.get("files", [])
    }


def _verified_key(item: dict) -> tuple[str, str, str, int]:
    return item.get("component_id"), item.get("relative_path"), item.get("sha256"), item.get("size")


def adopt_existing_installation(
    db,
    settings,
    *,
    project_id: str,
    path: str,
    snapshot: str,
    name: str = "shine-h3",
    port: int = 8188,
    actor: str,
) -> dict:
    """Record a locally confirmed installation after exact H3 weight hashing."""
    from shine.comfyops.deployment import load_default_catalog

    require_project(db, project_id)
    catalog = load_default_catalog()
    selected = local_path(path)
    report = inspect(str(selected), catalog)
    if report["snapshot"] != snapshot:
        raise HTTPException(409, "已有目录或模型清单变化，请重新检测后确认；本次没有下载任何文件。")
    if report["software"].get("version_status") != "INSTALLED_VERSION_MATCH":
        raise HTTPException(409, "EXISTING_COMFY_VERSION_NOT_PINNED")
    python_value = report["software"].get("runtime", {}).get("python_executable")
    if not python_value or not Path(python_value).is_file():
        raise HTTPException(409, "EXISTING_COMFY_PYTHON_NOT_FOUND")

    verified_files = _verified_existing_files(db, report)
    verified_keys = {_verified_key(item) for item in verified_files}
    required = _required_h3_files(catalog)
    missing = sorted(required - verified_keys)
    if missing:
        raise HTTPException(409, {
            "code": "EXISTING_H3_MODEL_FILES_INCOMPLETE",
            "missing_components": sorted({item[0] for item in missing}),
            "downloaded_bytes": 0,
        })
    matches = {
        (item["component_id"], item["relative_path"], item["sha256"], item["size"]): item
        for item in report.get("matches", [])
    }
    root = comfy_root(selected)
    if root is None:
        raise HTTPException(422, "现有目录不是有效的 ComfyUI 安装目录。")
    components = {component["component_id"]: component for component in catalog.get("components", [])}
    for key in required:
        item = matches.get(key)
        component = components.get(key[0]) or {}
        family = component.get("destination_family")
        relative = Path(key[1])
        models_root = root / "models"
        candidates = item.get("sources", []) if item else []
        allowed_paths = {
            (models_root / folder / relative).resolve()
            for folder in ((family, *_MODEL_FAMILY_ALIASES.get(family, ())) if family else ())
        } if not relative.is_absolute() and ".." not in relative.parts else set()
        source_path = Path(candidates[0]).resolve() if len(candidates) == 1 else None
        if (not allowed_paths or len(candidates) != 1 or source_path not in allowed_paths
                or not source_path.is_relative_to(models_root.resolve())):
            raise HTTPException(409, "EXISTING_H3_MODEL_PATH_UNAMBIGUOUS_REQUIRED")
    if any((event.payload or {}).get("snapshot") == snapshot and (event.payload or {}).get("rejected_files")
           for event in db.scalars(select(AuditEvent).where(
               AuditEvent.event_type == "EXISTING_COMFY_INSTALLATION_VERIFIED",
               AuditEvent.entity_type == "deployment",
               AuditEvent.entity_id == report["catalog_id"],
           )).all()):
        raise HTTPException(409, "EXISTING_MODEL_HASH_REJECTIONS_PRESENT")

    from shine.comfyops.deployment import ADAPTER_DIR
    from shine.comfyops.managed_recipe import adapter_identity

    adapter_dir = root / "custom_nodes" / "shine_comfy_handoff"
    if not adapter_dir.is_dir() or adapter_identity(adapter_dir) != adapter_identity(ADAPTER_DIR):
        raise HTTPException(409, "EXISTING_COMFY_HANDOFF_ADAPTER_MISMATCH")
    try:
        node_fingerprints = _node_fingerprints(root, catalog)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc

    identity = instance_identity("local", port)
    prior = db.get(ComfyInstance, identity)
    replaced_probe_row = False
    if prior is not None:
        adoption = prior.payload.get("adoption") or {}
        if prior.payload.get("adopted"):
            if (adoption.get("snapshot") == snapshot
                    and Path(prior.payload.get("root", "")).resolve() == root.resolve()):
                return {
                    "status": "ALREADY_ADOPTED",
                    "instance_id": prior.id,
                    "installed_h3_bytes": sum(item[3] for item in required),
                    "copied_bytes": 0,
                    "downloaded_bytes": 0,
                }
            raise HTTPException(409, "INSTANCE_IDENTITY_CHANGED")
        if prior.payload.get("managed") or prior.payload.get("is_mock"):
            # A managed (or fake) row that was never adopted is a real claim on
            # this identity; only a read-only detection row may be taken over.
            raise HTTPException(409, "INSTANCE_IDENTITY_CHANGED")
        # The local-mode inventory probe writes an evidence row for the same
        # deterministic identity before any adoption exists.  Detecting an
        # installation is exactly what precedes confirming it, so the first
        # adoption takes that row over instead of blocking the operator on a
        # row no one ever claimed.
        replaced_probe_row = True

    h3_verified_files = [item for item in verified_files if _verified_key(item) in required]
    review_components = {
        component["component_id"]: len(component.get("files", []))
        for component in catalog.get("components", []) if component.get("kind") == "review-model"
    }
    review_counts = {component: sum(1 for item in verified_files if item.get("component_id") == component)
                     for component in review_components}
    adoption = {
        "version": 1,
        "snapshot": snapshot,
        "catalog_id": catalog["catalog_id"],
        "source_revision": report["software"].get("actual_revision"),
        "python": str(Path(python_value).resolve()),
        "python_sha256": hashlib.sha256(Path(python_value).read_bytes()).hexdigest(),
        "node_fingerprints": node_fingerprints,
        "verified_h3_files": [
            {key: item[key] for key in ("component_id", "relative_path", "sha256", "size")}
            for item in h3_verified_files
        ],
        "review_model_file_counts": review_counts,
        "review_model_expected_counts": review_components,
        "confirmed_by": actor,
    }
    plan_payload = {
        "kind": "EXISTING_COMFY_ADOPTION",
        "project_id": project_id,
        "instance_id": identity,
        "root": str(root),
        "snapshot": snapshot,
        "installed_h3_bytes": sum(item["size"] for item in h3_verified_files),
        "copied_bytes": 0,
        "downloaded_bytes": 0,
    }
    plan = InstallationPlan(
        project_id=project_id,
        payload=plan_payload,
        sha256=graph_hash(plan_payload),
        status="COMPLETED",
    )
    db.add(plan)
    db.flush()
    attempt = InstallationAttempt(
        plan_id=plan.id,
        payload={"kind": "EXISTING_COMFY_ADOPTION", "is_mock": False, "snapshot": snapshot},
        sha256=graph_hash({"kind": "EXISTING_COMFY_ADOPTION", "is_mock": False, "snapshot": snapshot}),
        status="COMPLETED",
    )
    db.add(attempt)
    db.flush()
    payload = {
        "managed": True,
        "adopted": True,
        "external_installation": True,
        "name": name,
        "port": port,
        "root": str(root),
        "python": adoption["python"],
        "is_mock": False,
        "status_origin": "USER_CONFIRMED_EXISTING_INSTALLATION",
        "adoption": adoption,
        "last_attempt": attempt.id,
        "process": None,
    }
    attempt.payload = {
        "kind": "EXISTING_COMFY_ADOPTION",
        "is_mock": False,
        "snapshot": snapshot,
        "result": payload,
        "plan_sha256": plan.sha256,
    }
    attempt.sha256 = graph_hash(attempt.payload)
    if prior is not None:
        prior.payload = payload
        prior.sha256 = graph_hash(payload)
        prior.status = "STOPPED"
    else:
        db.add(ComfyInstance(id=identity, payload=payload, sha256=graph_hash(payload), status="STOPPED"))
    add_audit(db, actor, "EXISTING_COMFY_INSTALLATION_ADOPTED", "comfy_instance", identity, {
        "snapshot": snapshot,
        "source_revision": adoption["source_revision"],
        "verified_h3_components": sorted({item["component_id"] for item in h3_verified_files}),
        "installed_h3_bytes": plan_payload["installed_h3_bytes"],
        "review_model_file_counts": review_counts,
        "review_model_expected_counts": review_components,
        "copied_bytes": 0,
        "downloaded_bytes": 0,
        "user_install_modified": False,
        "replaced_probe_row": replaced_probe_row,
    }, project_id=project_id)
    db.commit()
    return {
        "status": "ADOPTED",
        "instance_id": identity,
        "installed_h3_bytes": plan_payload["installed_h3_bytes"],
        "copied_bytes": 0,
        "downloaded_bytes": 0,
        "review_model_file_counts": review_counts,
        "review_model_expected_counts": review_components,
        "node_fingerprints": len(node_fingerprints),
        "replaced_probe_row": replaced_probe_row,
    }


def adopted_installation_blockers(instance, catalog: dict) -> list[str]:
    """Revalidate the selected install's identity without rehashing model weights."""
    from shine.comfyops.existing_installation import inspect

    payload = instance.payload
    adoption = payload.get("adoption") or {}
    blockers = []
    try:
        root = comfy_root(local_path(payload.get("root", "")))
        if root is None or str(root) != payload.get("root"):
            return ["ADOPTED_COMFY_ROOT_CHANGED"]
        report = inspect(str(root), catalog)
        # The catalog's own file membership may change (for example when an
        # optional reviewer package drops repository metadata). That is not a
        # change to the user's installation. Only the H3 files the user actually
        # confirmed must still be present; model bytes are never rehashed here.
        present = {
            (row.get("component_id"), row.get("relative_path"))
            for row in report.get("matches", [])
        }
        confirmed = {
            (item.get("component_id"), item.get("relative_path"))
            for item in adoption.get("verified_h3_files", [])
        }
        if not confirmed <= present:
            blockers.append("ADOPTED_COMFY_FILES_CHANGED")
        if report.get("software", {}).get("actual_revision") != adoption.get("source_revision"):
            blockers.append("ADOPTED_COMFY_REVISION_CHANGED")
        python = Path(adoption.get("python", ""))
        if (not python.is_file() or python.resolve() != python
                or hashlib.sha256(python.read_bytes()).hexdigest() != adoption.get("python_sha256")):
            blockers.append("ADOPTED_COMFY_PYTHON_CHANGED")
        if _node_fingerprints(root, catalog) != adoption.get("node_fingerprints"):
            blockers.append("ADOPTED_COMFY_NODES_CHANGED")
        required = _required_h3_files(catalog)
        verified = {_verified_key(item) for item in adoption.get("verified_h3_files", [])}
        if not required <= verified:
            blockers.append("ADOPTED_H3_MODEL_EVIDENCE_INCOMPLETE")
    except (OSError, ValueError, HTTPException):
        blockers.append("ADOPTED_COMFY_IDENTITY_UNAVAILABLE")
    return sorted(set(blockers))

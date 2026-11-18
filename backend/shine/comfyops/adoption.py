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
from shine.comfyops.host_env import probe_comfy_service
from shine.comfyops.instances import instance_identity
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan
from shine.models import AuditEvent
from shine.services import add_audit, require_project

_CODE_SUFFIXES = {".py", ".pyi", ".pyd", ".dll", ".json", ".toml", ".yaml", ".yml", ".ini"}
_IGNORED_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}


def runtime_root(settings, root: Path, instance_id: str) -> Path:
    return settings.comfyops_dir / "adopted-runtime" / graph_hash({
        "root": str(root), "instance_id": instance_id,
    })[:24]


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
    from shine.comfyops.existing_installation import resolve_node_pack_dirs
    pack_dirs, _missing = resolve_node_pack_dirs(root, catalog)
    result = {}
    for component_id, directory in pack_dirs.items():
        result[component_id] = _node_tree_fingerprint(root / "custom_nodes" / directory)
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


def _required_capability_files(catalog: dict, capability: str) -> set[tuple[str, str, str, int]]:
    """The bytes whose presence *this* capability needs, and no others.

    Ownership evidence (adapter, python, revision, node tree) is the same for
    every capability.  What differs is the weight set: an installation prepared
    only for 素材出图 holds the Qwen image files and no MiniMax weights, and
    asking it for the video model would force a download nobody requested.
    """
    if capability == "h3":
        from shine.comfyops.variant_readiness import required_h3_components

        return {
            (component["component_id"], item["relative_path"], item["sha256"], item["size"])
            for component in required_h3_components(catalog)
            for item in component.get("files") or []
        }
    if capability == "image":
        return {
            (component["component_id"], item["relative_path"], item["sha256"], item["size"])
            for component in catalog.get("components", [])
            if component.get("kind") == "model"
            for item in component.get("files", [])
        }
    raise HTTPException(409, "ADOPTION_CAPABILITY_UNKNOWN")


def _verified_key(item: dict) -> tuple[str, str, str, int]:
    return item.get("component_id"), item.get("relative_path"), item.get("sha256"), item.get("size")


def _receipt_file_paths(report: dict) -> dict[tuple[str, str], Path]:
    """The one file each verified match was read from.

    The audit record stores per-file hashes but not paths (absolute paths are
    redacted from audit payloads).  The inspection snapshot *does* bind every
    candidate path to its size and modification time, and the verification was
    performed against that exact snapshot.  Reusing a path here therefore
    reuses the same evidence standard as the receipt itself: same snapshot,
    same size, same mtime.  When two different files carry the same size (a
    duplicate copy), the choice is ambiguous and no receipt is written for
    that component instead of guessing.
    """
    found: dict[tuple[str, str], Path] = {}
    for match in report.get("matches", []):
        sources = [Path(row) for row in (match.get("sources") or [])]
        if not sources:
            continue
        identities = set()
        for source in sources:
            try:
                stat = source.stat()
            except OSError:
                continue
            identities.add((getattr(stat, "st_dev", None), getattr(stat, "st_ino", None),
                            stat.st_size, getattr(stat, "st_mtime_ns", None)))
        if len(identities) == 1:
            found[(match["component_id"], match["relative_path"])] = sources[0]
    return found


def _save_adoption_receipts(db, report: dict, catalog: dict, verified_files: list[dict]) -> int:
    """Write path-bound receipts so readiness can reuse the verified bytes.

    Older receipts recorded one package directory.  An external installation
    keeps its weights in the user's own ``models/<family>`` tree, so the
    receipt must record the exact file that was hashed.  Nothing is copied and
    no byte is read again here.
    """
    from pathlib import Path as _Path

    from shine.comfyops.existing_installation import declared_model_family_roots
    from shine.comfyops.verification_receipts import save_receipt

    verified_keys = {_verified_key(item) for item in verified_files}
    paths = _receipt_file_paths(report)
    root = _Path(report["root"])
    allowed_roots = [root, *[directory for dirs in declared_model_family_roots(root).values()
                             for directory in dirs]]
    written = 0
    for component in catalog.get("components", []):
        if component.get("kind") not in {"model", "review-model"}:
            continue
        files = component.get("files") or []
        if not files:
            continue
        if any((component["component_id"], item["relative_path"], item["sha256"], item["size"])
               not in verified_keys for item in files):
            continue
        file_paths = {}
        for item in files:
            source = paths.get((component["component_id"], item["relative_path"]))
            if source is None:
                break
            file_paths[item["relative_path"]] = source
        else:
            save_receipt(
                db,
                variant_id=(catalog.get("variant") or {}).get("variant_id")
                if isinstance(catalog.get("variant"), dict) else None,
                catalog_id=catalog.get("catalog_id"),
                catalog_version=catalog.get("catalog_version"),
                component=component,
                root=root,
                directory=root,
                file_paths=file_paths,
                allowed_roots=allowed_roots,
            )
            written += 1
    return written


def adopt_existing_installation(
    db,
    settings,
    *,
    project_id: str | None,
    path: str,
    snapshot: str,
    name: str = "shine-h3",
    port: int = 8188,
    actor: str,
    capability: str = "h3",
    catalog: dict | None = None,
    inspected: dict | None = None,
    verified: list[dict] | None = None,
) -> dict:
    """Record a locally confirmed installation after exact weight hashing.

    ``capability`` names which weight set the confirmation covers.  It is not a
    second adoption mechanism: the instance row, the adapter check, the python
    identity, the node fingerprints and the receipts are the same objects.  It
    only decides which bytes must already be present, so a machine that wants
    素材出图 is never asked to download a video model first, and an H3 adoption
    never becomes valid for image work by accident.
    """
    from shine.comfyops.deployment import (
        IMAGE_CAPABILITY_CATALOG,
        load_default_catalog,
        load_deployment_catalog,
    )
    from shine.comfyops.existing_installation import model_sources
    from shine.comfyops.verification_receipts import installed_directory

    if capability not in {"h3", "image"}:
        raise HTTPException(409, "ADOPTION_CAPABILITY_UNKNOWN")
    if project_id is not None:
        require_project(db, project_id)
    catalog = catalog or (load_deployment_catalog(IMAGE_CAPABILITY_CATALOG)
                          if capability == "image" else load_default_catalog())
    selected = local_path(path)
    report = inspected if inspected is not None else inspect(str(selected), catalog)
    if report["snapshot"] != snapshot:
        raise HTTPException(409, "已有目录或模型清单变化，请重新检测后确认；本次没有下载任何文件。")
    service = probe_comfy_service(port)
    software = report["software"]
    version_status = software.get("version_status")
    version_source = software.get("version_source")
    version_confidence = software.get("version_confidence")
    if (version_status == "INSTALLED_VERSION_UNKNOWN" and service["is_comfyui"]
            and service.get("version")):
        # The running service is itself a trusted identity source.  It proves
        # which ComfyUI answered, not that it is the pinned Git revision.
        version_status = "INSTALLED_VERSION_OBSERVED"
        version_source = "/system_stats"
        version_confidence = "MEDIUM"
    if version_status not in {
        "INSTALLED_VERSION_MATCH",
        "INSTALLED_VERSION_MODIFIED",
        "INSTALLED_VERSION_MISMATCH",
        "INSTALLED_VERSION_OBSERVED",
    }:
        # Neither a local declaration nor the running service can name the
        # software.  Registering it would be a guess.
        raise HTTPException(409, "EXISTING_COMFY_VERSION_UNVERIFIED")
    python_value = report["software"].get("runtime", {}).get("python_executable")
    if not python_value or not Path(python_value).is_file():
        raise HTTPException(409, "EXISTING_COMFY_PYTHON_NOT_FOUND")

    verified_files = verified if verified is not None else _verified_existing_files(db, report)
    verified_by_key = {_verified_key(item): item for item in verified_files}
    verified_keys = {_verified_key(item) for item in verified_files}
    required = _required_capability_files(catalog, capability)
    missing = sorted(required - verified_keys)
    if missing:
        raise HTTPException(409, {
            "code": "EXISTING_MODEL_FILES_INCOMPLETE" if capability == "image"
            else "EXISTING_H3_MODEL_FILES_INCOMPLETE",
            "capability": capability,
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
        candidates = item.get("sources", []) if item else []
        served = model_sources(root / "models", family, Path(key[1]),
                               report.get("declared_model_family_roots") or {}) if family else []
        # Legacy audit rows did not keep the verified path. That path can only
        # be recovered when the original inspection offered one candidate.
        verified_source = verified_by_key[key].get("source")
        if not verified_source and len(candidates) == 1:
            verified_source = candidates[0]
        package_dir = installed_directory(settings, component)
        installed = package_dir / key[1] if package_dir is not None else None
        if installed is not None and installed.is_file() and verified_source and installed == Path(verified_source):
            continue
        if len(served) != 1 or not verified_source or served[0] != Path(verified_source):
            raise HTTPException(409, "EXISTING_H3_MODEL_PATH_UNAMBIGUOUS_REQUIRED")
    if verified is None and any(
           (event.payload or {}).get("snapshot") == snapshot and (event.payload or {}).get("rejected_files")
           for event in db.scalars(select(AuditEvent).where(
               AuditEvent.event_type == "EXISTING_COMFY_INSTALLATION_VERIFIED",
               AuditEvent.entity_type == "deployment",
               AuditEvent.entity_id == report["catalog_id"],
           )).all()):
        raise HTTPException(409, "EXISTING_MODEL_HASH_REJECTIONS_PRESENT")

    try:
        node_fingerprints = _node_fingerprints(root, load_default_catalog())
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc

    # R5 B1：物理安装身份稳定。同一 root 的历史行（无论当年按哪个端口收编）
    # 被继承并保留原 ID；端口只是这一行上的可变端点。只有从未收编过的 root
    # 才使用新的稳定身份；探测证据行仍按原端口身份接管。
    from shine.comfyops.instances import adopted_instance_identity, find_adopted_instance

    prior = find_adopted_instance(db, root, port)
    replaced_probe_row = False
    if prior is not None:
        identity = prior.id
    else:
        probe = db.get(ComfyInstance, instance_identity("local", port))
        if probe is not None and not probe.payload.get("adopted"):
            if probe.payload.get("managed") or probe.payload.get("is_mock"):
                # A managed (or fake) row is a real claim on this endpoint by
                # this workbench's own instance; it is never overwritten.
                raise HTTPException(409, "INSTANCE_IDENTITY_CHANGED")
            # The local-mode inventory probe writes an evidence row for the
            # deterministic port identity before any adoption exists. The
            # first adoption takes that row over instead of blocking on a row
            # no one ever claimed.
            prior = probe
            identity = probe.id
            replaced_probe_row = True
        elif probe is not None and probe.payload.get("adopted"):
            # Another installation's adopted row owns this port identity.
            # History is never rewritten: the new installation gets its own
            # stable root identity instead of a dead-end 409.
            identity = adopted_instance_identity(root)
        else:
            identity = adopted_instance_identity(root)
    prior_adoption: dict = {}
    if prior is not None:
        prior_adoption = prior.payload.get("adoption") or {}
        if prior.payload.get("adopted"):
            if prior.payload.get("port") != port:
                # R5 B1/INV-03：端点迁移只改这一行的端口与服务探测事实；
                # 行 ID、已核验字节证据、节点指纹全部保留，历史不重写。
                moved = dict(prior.payload)
                moved["port"] = port
                moved_adoption = dict(prior_adoption)
                moved_adoption["service_identity"] = {
                    "port": port,
                    "is_comfyui": service["is_comfyui"],
                    "version": service.get("version"),
                    "version_source": service.get("version_source"),
                }
                moved["adoption"] = moved_adoption
                prior.payload = moved
                prior.sha256 = graph_hash(moved)
                prior_adoption = moved_adoption
                add_audit(db, actor, "ADOPTED_INSTALLATION_ENDPOINT_CHANGED",
                          "comfy_instance", prior.id, {"port": port}, project_id=project_id)
                db.commit()
            covered = set(prior_adoption.get("capabilities") or ["h3"])
            old_files = prior_adoption.get("verified_files") or [
                {**item, "capability": "h3"} for item in prior_adoption.get("verified_h3_files") or []
            ]
            covered_here = {_verified_key(item) for item in old_files
                            if (item.get("capability") or "h3") == capability}
            if (capability in covered and required <= covered_here
                    and not adopted_installation_blockers(prior, catalog, capability=capability)):
                return {
                    "status": "ALREADY_ADOPTED",
                    "instance_id": prior.id,
                    "capability": capability,
                    "installed_h3_bytes": sum(item["size"] for item in prior_adoption.get("verified_h3_files") or []),
                    "copied_bytes": 0,
                    "downloaded_bytes": 0,
                }
            if prior.status != "STOPPED" or prior.payload.get("process"):
                raise HTTPException(409, "STOP_OWNED_INSTANCE_BEFORE_CONFIGURATION")
        elif prior.payload.get("managed") or prior.payload.get("is_mock"):
            # A managed (or fake) row that was never adopted is a real claim on
            # this identity; only a read-only detection row may be taken over.
            raise HTTPException(409, "INSTANCE_IDENTITY_CHANGED")
        else:
            # The local-mode inventory probe writes an evidence row for the same
            # deterministic identity before any adoption exists.  Detecting an
            # installation is exactly what precedes confirming it, so the first
            # adoption takes that row over instead of blocking the operator on a
            # row no one ever claimed.
            replaced_probe_row = True

    capability_files = [item for item in verified_files if _verified_key(item) in required]
    h3_verified_files = [item for item in verified_files
                         if _verified_key(item) in _required_h3_files(catalog)]
    covered_capabilities = sorted({*(prior_adoption.get("capabilities") or []), capability})
    previous_files = prior_adoption.get("verified_files") or [
        {**item, "capability": "h3"} for item in prior_adoption.get("verified_h3_files") or []
    ]
    confirmed_files = [item for item in previous_files if (item.get("capability") or "h3") != capability]
    confirmed_files.extend(
        {**{key: item[key] for key in ("component_id", "relative_path", "sha256", "size")},
         **({"source": item["source"]} if item.get("source") else {}),
         "capability": capability} for item in capability_files
    )
    receipt_count = (_save_adoption_receipts(db, report, catalog, verified_files)
                     if verified is None else len({row["component_id"] for row in verified_files}))
    review_components = {
        component["component_id"]: len(component.get("files", []))
        for component in catalog.get("components", []) if component.get("kind") == "review-model"
    }
    review_counts = {component: sum(1 for item in verified_files if item.get("component_id") == component)
                     for component in review_components}
    adoption = {
        "version": 3,
        "snapshot": snapshot,
        "catalog_id": catalog["catalog_id"],
        "capabilities": covered_capabilities,
        "source_revision": report["software"].get("actual_revision"),
        "version_status": version_status,
        "version_source": version_source,
        "version_confidence": version_confidence,
        "service_identity": {
            "port": port,
            "is_comfyui": service["is_comfyui"],
            "version": service.get("version"),
            "version_source": service.get("version_source"),
        },
        "node_packages": {
            "count": len((report.get("node_packages") or {}).get("packages") or []),
            "expected_by_catalog": (report.get("node_packages") or {}).get("expected_by_catalog") or [],
        },
        "python": str(Path(python_value).resolve()),
        "python_sha256": hashlib.sha256(Path(python_value).read_bytes()).hexdigest(),
        "node_fingerprints": node_fingerprints,
        "verified_h3_files": (prior_adoption.get("verified_h3_files") or []) if capability != "h3" else [
            {key: item[key] for key in ("component_id", "relative_path", "sha256", "size")}
            for item in h3_verified_files
        ],
        #: Every capability's confirmed bytes, keyed by capability, so a later
        #: re-validation asks the same question the adoption answered.
        "verified_files": confirmed_files,
        "review_model_file_counts": review_counts,
        "review_model_expected_counts": review_components,
        "path_bound_receipts": receipt_count,
        "confirmed_by": actor,
    }
    plan_payload = {
        "kind": "EXISTING_COMFY_ADOPTION",
        "project_id": project_id,
        "instance_id": identity,
        "root": str(root),
        "snapshot": snapshot,
        "capability": capability,
        "installed_h3_bytes": sum(item["size"] for item in h3_verified_files),
        "installed_capability_bytes": sum(item["size"] for item in capability_files),
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
        "capability": capability,
        "capabilities": covered_capabilities,
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
        "capability": capability,
        "capabilities": covered_capabilities,
        "installed_h3_bytes": plan_payload["installed_h3_bytes"],
        "installed_capability_bytes": plan_payload["installed_capability_bytes"],
        "copied_bytes": 0,
        "downloaded_bytes": 0,
        "review_model_file_counts": review_counts,
        "review_model_expected_counts": review_components,
        "node_fingerprints": len(node_fingerprints),
        "replaced_probe_row": replaced_probe_row,
    }


#: Short-lived cache of the adopted-installation revalidation, keyed by the
#: instance payload hash + catalog hash + capability. The check walks the
#: installation, hashes the interpreter and fingerprints every node pack; it is
#: read on every status/summary/availability projection, so repeating it cold
#: made a single page load take seconds and timed out the connection probe.
#: The key changes whenever the adoption record or catalog changes; the TTL
#: bounds how long an on-disk change (a node pack added/removed without touching
#: the record) can go unnoticed. Downstream render still verifies weights and
#: nodes independently, so this is a display-latency cache, not the only gate.
_ADOPTION_BLOCKERS_CACHE: dict[tuple[str, str, str, int], tuple[float, list[str]]] = {}
_ADOPTION_BLOCKERS_TTL = 3.0


def adopted_installation_blockers(instance, catalog: dict, *, capability: str = "h3") -> list[str]:
    import time

    from shine.comfyops import existing_installation as _ei

    # The computation late-imports existing_installation.inspect; key by that
    # callable's identity so substituting it (a refreshed read, or a test)
    # invalidates the cached projection instead of serving a stale verdict.
    key = (str(instance.sha256), graph_hash(catalog), capability, id(_ei.inspect))
    now = time.monotonic()
    hit = _ADOPTION_BLOCKERS_CACHE.get(key)
    if hit is not None and now - hit[0] < _ADOPTION_BLOCKERS_TTL:
        return list(hit[1])
    result = _adopted_installation_blockers_uncached(instance, catalog, capability=capability)
    if len(_ADOPTION_BLOCKERS_CACHE) >= 64:
        _ADOPTION_BLOCKERS_CACHE.clear()
    _ADOPTION_BLOCKERS_CACHE[key] = (now, result)
    return list(result)


def _adopted_installation_blockers_uncached(instance, catalog: dict, *, capability: str = "h3") -> list[str]:
    """Revalidate the selected install's identity without rehashing model weights.

    ``capability`` scopes which confirmed bytes must still be present.  The
    ownership chain - root, revision, python digest, node fingerprints - is
    always checked in full; only the weight set follows the capability.  An
    image-only adoption therefore cannot be invalidated by an absent video
    model, and an H3 adoption cannot start rendering video because an image
    recipe asked a laxer question.
    """
    from shine.comfyops.deployment import load_default_catalog
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
        # change to the user's installation. Only the files the user actually
        # confirmed must still be present; model bytes are never rehashed here.
        present = {
            (row.get("component_id"), row.get("relative_path"))
            for row in report.get("matches", [])
        }
        confirmed_rows = [row for row in adoption.get("verified_files") or []
                          if (row.get("capability") or "h3") == capability]
        if capability == "h3" and not confirmed_rows:
            # Adoption records written before capabilities existed only carry
            # the H3 set; that set *is* the H3 evidence.
            confirmed_rows = adoption.get("verified_h3_files", [])
        for item in confirmed_rows:
            source = Path(item["source"]) if item.get("source") else None
            if (source is not None and source.is_absolute() and source.resolve() == source
                    and source.is_file() and source.stat().st_size == item.get("size")):
                present.add((item["component_id"], item["relative_path"]))
        confirmed = {
            (item.get("component_id"), item.get("relative_path"))
            for item in confirmed_rows
        }
        if not confirmed <= present:
            blockers.append("ADOPTED_COMFY_FILES_CHANGED")
        if report.get("software", {}).get("actual_revision") != adoption.get("source_revision"):
            blockers.append("ADOPTED_COMFY_REVISION_CHANGED")
        python = Path(adoption.get("python", ""))
        if (not python.is_file() or python.resolve() != python
                or hashlib.sha256(python.read_bytes()).hexdigest() != adoption.get("python_sha256")):
            blockers.append("ADOPTED_COMFY_PYTHON_CHANGED")
        expected_nodes = adoption.get("node_fingerprints") or {}
        # Older image-only records pinned only the packs visible in the image
        # catalog. Their evidence still applies to those same packs.
        actual_nodes = _node_fingerprints(root, load_default_catalog())
        if any(actual_nodes.get(name) != digest for name, digest in expected_nodes.items()):
            blockers.append("ADOPTED_COMFY_NODES_CHANGED")
        required = _required_capability_files(catalog, capability)
        verified = {(item.get("component_id"), item.get("relative_path"),
                     item.get("sha256"), item.get("size"))
                    for item in confirmed_rows}
        if not required <= verified:
            blockers.append("ADOPTED_H3_MODEL_EVIDENCE_INCOMPLETE" if capability == "h3"
                            else "ADOPTED_CAPABILITY_MODEL_EVIDENCE_INCOMPLETE")
    except (OSError, ValueError, HTTPException):
        blockers.append("ADOPTED_COMFY_IDENTITY_UNAVAILABLE")
    return sorted(set(blockers))

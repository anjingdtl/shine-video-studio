"""Machine-level resource discovery: bounded batch scans and a cross-drive index.

The setup page used to inspect only the first ComfyUI a shallow probe returned.
Weights, node packs and workflows, however, are routinely spread over several
drives and directories that no ComfyUI installation declares.  This module
keeps one durable, machine-level discovery state:

* the person's search directories (added via the native picker or a pasted
  absolute path) are stored separately from installation and output directories;
* fixed drives and declared roots are scanned in bounded batches, each with its
  own coverage record, so an early empty result reads as "still scanning" and a
  slow or unreadable area is named instead of silently skipped;
* files are matched to the pinned catalogues by size first and only candidates
  are ever hashed later (by the existing verification path).  A renamed file is
  therefore eligible; a same-named wrong file is not trusted.

Nothing here executes a discovered program, copies a byte or starts a renderer.
The index is evidence of what is on disk; verification and registration stay
with the existing reconcile/installation machinery.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from shine.comfyops import deployment
from shine.comfyops.existing_installation import (
    _SCAN_SKIP_DIRECTORIES,
    CONFIGURED_ROOT_DEPTH,
    DRIVE_SCAN_DEPTH,
    _is_comfy_directory,
    _pack_alias,
    fixed_drives,
    local_path,
)
from shine.models import AppSetting, utcnow

STATE_KEY = "resource-discovery-state"
SCAN_BATCH_DIRECTORY_BUDGET = 4_000
SCAN_BATCH_TIME_BUDGET_S = 2.5
MAX_USER_DIRECTORIES = 24
MAX_MODEL_CANDIDATES = 4_000
MAX_WORKFLOW_CANDIDATES = 400

IMAGE_CATALOG_ID = "qwen-image-2.1-windows-x64"


def _catalogues() -> list[dict]:
    rows = []
    for loader in (deployment.load_default_catalog,
                   lambda: deployment.load_deployment_catalog(IMAGE_CATALOG_ID)):
        try:
            rows.append(loader())
        except (OSError, ValueError, FileNotFoundError):
            continue
    return rows


def _model_specs() -> dict[int, list[dict]]:
    """Catalog model files grouped by byte size — the cheap candidate filter."""
    specs: dict[int, list[dict]] = {}
    for catalog in _catalogues():
        for component in catalog.get("components", []):
            if component.get("kind") not in {"model", "review-model"}:
                continue
            for spec in component.get("files", []):
                specs.setdefault(int(spec["size"]), []).append({
                    "catalog_id": catalog["catalog_id"],
                    "component_id": component["component_id"],
                    "relative_path": spec["relative_path"],
                    "sha256": spec["sha256"],
                    "size": int(spec["size"]),
                })
    return specs


def _node_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for catalog in _catalogues():
        for component in catalog.get("components", []):
            if component.get("kind") == "node":
                aliases[_pack_alias(component["component_id"])] = component["component_id"]
    return aliases


def read_state(db) -> dict[str, Any]:
    row = db.get(AppSetting, STATE_KEY)
    if row is not None and isinstance(row.value, dict):
        return dict(row.value)
    return {
        "version": 1,
        "directories": [],
        "scan": {"status": "IDLE", "queue": [], "coverage": {}, "started_at": None,
                 "updated_at": None, "message": "还没有开始检索"},
        "resources": {"comfy_roots": [], "model_candidates": [], "node_dirs": [],
                      "workflows": []},
        "updated_at": None,
    }


def _save(db, state: dict[str, Any]) -> None:
    state["updated_at"] = utcnow().isoformat()
    row = db.get(AppSetting, STATE_KEY)
    if row is None:
        db.add(AppSetting(key=STATE_KEY, value=state))
    else:
        row.value = state


def list_directories(db) -> list[dict]:
    return list(read_state(db).get("directories") or [])


def add_directory(db, path_value: str, *, actor: str = "local-user") -> dict:
    """Register one local search directory (drive root, downloads, models...)."""
    from shine.services import add_audit

    path = local_path(path_value)
    if not path.is_dir():
        from fastapi import HTTPException
        raise HTTPException(422, "这个目录在本机读不到，请确认路径存在。")
    state = read_state(db)
    directories = state.get("directories") or []
    resolved = str(path)
    if any(row["path"] == resolved for row in directories):
        return {"status": "ALREADY_PRESENT", "directory": next(
            row for row in directories if row["path"] == resolved)}
    if len(directories) >= MAX_USER_DIRECTORIES:
        from fastapi import HTTPException
        raise HTTPException(409, f"检索目录最多 {MAX_USER_DIRECTORIES} 个，请先删除不用的。")
    row = {"id": uuid4().hex[:12], "path": resolved, "added_at": utcnow().isoformat()}
    directories.append(row)
    state["directories"] = directories
    _enqueue_seed(state, resolved, CONFIGURED_ROOT_DEPTH, source="user")
    _save(db, state)
    add_audit(db, actor, "RESOURCE_SEARCH_DIRECTORY_ADDED", "resource_discovery", row["id"],
              {"path": resolved})
    db.commit()
    return {"status": "ADDED", "directory": row}


def remove_directory(db, directory_id: str, *, actor: str = "local-user") -> dict:
    from shine.services import add_audit

    state = read_state(db)
    directories = state.get("directories") or []
    row = next((item for item in directories if item["id"] == directory_id), None)
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(404, "没有这条检索目录记录。")
    state["directories"] = [item for item in directories if item["id"] != directory_id]
    state["scan"]["queue"] = [seed for seed in state["scan"]["queue"]
                              if seed.get("source") != "user" or seed.get("root") != row["path"]]
    _save(db, state)
    add_audit(db, actor, "RESOURCE_SEARCH_DIRECTORY_REMOVED", "resource_discovery",
              directory_id, {"path": row["path"]})
    db.commit()
    return {"status": "REMOVED", "path": row["path"]}


def _enqueue_seed(state: dict, root: str, depth: int, *, source: str) -> None:
    queue = state["scan"].setdefault("queue", [])
    if any(seed["root"] == root and seed["source"] == source for seed in queue):
        return
    coverage = state["scan"].setdefault("coverage", {})
    if coverage.get(root, {}).get("status") == "DONE" and source != "rescan":
        return
    queue.append({"root": root, "depth": depth, "source": source})


def start_scan(db, *, drives: bool = True, actor: str = "local-user") -> dict:
    """Queue one bounded deep scan over fixed drives and declared directories."""
    from shine.services import add_audit

    state = read_state(db)
    for row in state.get("directories") or []:
        _enqueue_seed(state, row["path"], CONFIGURED_ROOT_DEPTH, source="user")
    if drives:
        for drive in fixed_drives():
            _enqueue_seed(state, str(drive), DRIVE_SCAN_DEPTH, source="drive")
    probe_roots = os.environ.get("SHINE_COMFY_PROBE_ROOTS", "")
    for raw in probe_roots.split(os.pathsep):
        if raw.strip():
            try:
                _enqueue_seed(state, str(local_path(raw.strip())), CONFIGURED_ROOT_DEPTH,
                              source="probe_root")
            except Exception:  # noqa: BLE001 - an invalid env root is named, not fatal
                state["scan"].setdefault("unreadable", []).append(raw.strip())
    if not state["scan"]["queue"]:
        state["scan"]["message"] = "没有可检索的目录"
        _save(db, state)
        return {"status": "IDLE", "queued": 0}
    state["scan"]["status"] = "QUEUED"
    state["scan"]["started_at"] = utcnow().isoformat()
    state["scan"]["message"] = "检索已排队，后台分批进行"
    _save(db, state)
    add_audit(db, actor, "RESOURCE_SCAN_STARTED", "resource_discovery", STATE_KEY,
              {"queued": len(state["scan"]["queue"])})
    db.commit()
    return {"status": "QUEUED", "queued": len(state["scan"]["queue"])}


def _priority(entry) -> tuple[int, str]:
    name = entry.name.lower()
    if "comfy" in name:
        return (0, name)
    if name.startswith(("ai", "sd", "stable", "download", "tools", "apps", "models",
                        "model", "weight", "ckpt", "checkpoint")):
        return (1, name)
    return (2, name)


def _looks_like_workflow(path: Path) -> bool:
    name = path.name.lower()
    if not name.endswith(".json") or path.stat().st_size > 2_000_000:
        return False
    if not ("workflow" in name or "api" in name or "graph" in name
            or "workflow" in str(path.parent).lower()):
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            head = stream.read(8192)
        return '"class_type"' in head
    except OSError:
        return False


def _scan_seed(seed: dict, state: dict, specs: dict[int, list[dict]],
               aliases: dict[str, str], deadline: float) -> dict:
    """One bounded batch over one seed; returns its coverage record.

    A truncated batch persists its remaining directory stack, so the next
    batch continues where this one stopped instead of re-walking the same
    first directories forever.
    """
    root = Path(seed["root"])
    depth = int(seed["depth"])
    resources = state["resources"]
    comfy_seen = {row["root"] for row in resources["comfy_roots"]}
    model_seen = {(row["path"], row["component_id"]) for row in resources["model_candidates"]}
    node_seen = {row["path"] for row in resources["node_dirs"]}
    workflow_seen = {row["path"] for row in resources["workflows"]}
    unreadable: list[str] = []
    visited = 0
    truncated = False
    resume = seed.get("resume")
    if resume:
        stack: list[tuple[Path, int]] = [(Path(row[0]), int(row[1])) for row in resume]
    else:
        stack = [(root, 0)]
    while stack:
        if visited >= SCAN_BATCH_DIRECTORY_BUDGET or time.monotonic() >= deadline:
            truncated = True
            break
        directory, level = stack.pop()
        visited += 1
        try:
            if _is_comfy_directory(directory):
                resolved = str(directory.resolve())
                if resolved not in comfy_seen:
                    comfy_seen.add(resolved)
                    resources["comfy_roots"].append(
                        {"root": resolved, "found_via": seed["source"]})
                # The installation's own models tree is scanned for candidates too.
                models = directory / "models"
                if models.is_dir():
                    stack.append((models, level))
                continue
            if level >= depth:
                continue
            children = list(os.scandir(directory))
        except OSError:
            unreadable.append(str(directory))
            continue
        children.sort(key=_priority)
        for entry in children:
            name = entry.name.lower()
            if name.startswith(".") or name in _SCAN_SKIP_DIRECTORIES:
                continue
            try:
                if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if aliases.get(_pack_alias(entry.name)):
                        resolved = str(Path(entry.path).resolve())
                        if resolved not in node_seen:
                            node_seen.add(resolved)
                            resources["node_dirs"].append({
                                "path": resolved, "name": entry.name,
                                "component_id": aliases[_pack_alias(entry.name)],
                                "found_via": seed["source"],
                            })
                    stack.append((Path(entry.path), level + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
                for spec in specs.get(stat.st_size, []):
                    key = (entry.path, spec["component_id"])
                    if key in model_seen or len(resources["model_candidates"]) >= MAX_MODEL_CANDIDATES:
                        continue
                    model_seen.add(key)
                    resources["model_candidates"].append({
                        **spec, "path": entry.path,
                        "mtime_ns": getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)),
                        "device": getattr(stat, "st_dev", None),
                        "inode": getattr(stat, "st_ino", None),
                        "status": "PENDING_HASH", "found_via": seed["source"],
                    })
                if (len(resources["workflows"]) < MAX_WORKFLOW_CANDIDATES
                        and entry.path not in workflow_seen
                        and _looks_like_workflow(Path(entry.path))):
                    workflow_seen.add(entry.path)
                    resources["workflows"].append({"path": entry.path,
                                                   "found_via": seed["source"]})
            except OSError:
                unreadable.append(entry.path)
                continue
    prior = state["scan"].get("coverage", {}).get(str(root), {})
    record = {
        "root": str(root), "source": seed["source"],
        "status": "TRUNCATED" if truncated else "DONE",
        "directories_visited": int(prior.get("directories_visited", 0)) + visited,
        "unreadable": sorted(set(prior.get("unreadable", [])) | set(unreadable)),
        "depth": depth, "updated_at": utcnow().isoformat(),
    }
    if truncated:
        # The continuation cursor is part of the coverage record: a restarted
        # worker reads it back and resumes the same walk.
        record["pending"] = [[str(path), level] for path, level in stack[:8000]]
    return record


def advance_scan(db, *, budget_s: float = SCAN_BATCH_TIME_BUDGET_S) -> dict:
    """Advance the durable scan by one bounded batch (worker tick or API)."""
    state = read_state(db)
    scan = state["scan"]
    queue = scan.get("queue") or []
    if not queue:
        if scan.get("status") in {"QUEUED", "RUNNING"}:
            scan["status"] = "DONE"
            scan["message"] = "本轮检索完成"
            _save(db, state)
            db.commit()
        return {"status": scan.get("status") or "IDLE", "remaining": 0}
    scan["status"] = "RUNNING"
    seed = queue.pop(0)
    deadline = time.monotonic() + budget_s
    coverage = _scan_seed(seed, state, _model_specs(), _node_aliases(), deadline)
    scan.setdefault("coverage", {})[seed["root"]] = coverage
    if coverage["status"] == "TRUNCATED":
        # A truncated seed keeps its place with its continuation cursor: the
        # next batch resumes the same walk instead of starting over.
        seed["resume"] = coverage.pop("pending", [])
        queue.insert(0, seed)
        scan["message"] = f"正在检索 {seed['root']}（分批继续）"
    else:
        scan["message"] = f"已完成 {seed['root']} 的检索"
    scan["updated_at"] = utcnow().isoformat()
    if not queue:
        scan["status"] = "DONE"
        scan["message"] = "本轮检索完成"
    _save(db, state)
    db.commit()
    return {"status": scan["status"], "remaining": len(queue),
            "coverage": coverage}


def report(db) -> dict:
    """Read-only snapshot: coverage, candidates and what is still missing.

    A file found on disk is a *candidate*, never a verified byte; the report
    keeps those two facts apart so the page cannot turn a scan into a claim.
    """
    state = read_state(db)
    resources = state["resources"]
    scan = state["scan"]
    per_capability: dict[str, dict] = {}
    for catalog in _catalogues():
        capability = "image" if catalog["catalog_id"] == IMAGE_CATALOG_ID else "h3"
        bucket = per_capability.setdefault(capability, {
            "catalog_id": catalog["catalog_id"], "found": [], "missing": [],
        })
        for component in catalog.get("components", []):
            if component.get("kind") not in {"model", "review-model"}:
                continue
            for spec in component.get("files", []):
                sources = [row for row in resources["model_candidates"]
                           if row["component_id"] == component["component_id"]
                           and row["relative_path"] == spec["relative_path"]]
                entry = {"component_id": component["component_id"],
                         "relative_path": spec["relative_path"],
                         "size": spec["size"], "sha256": spec["sha256"],
                         "sources": [row["path"] for row in sources]}
                if sources:
                    bucket["found"].append(entry)
                else:
                    bucket["missing"].append(entry)
    coverage = scan.get("coverage") or {}
    return {
        "status": scan.get("status") or "IDLE",
        "message": scan.get("message"),
        "directories": state.get("directories") or [],
        "queue_remaining": len(scan.get("queue") or []),
        "coverage": coverage,
        "coverage_complete": bool(coverage) and all(
            row.get("status") == "DONE" for row in coverage.values())
        and not (scan.get("queue") or []),
        "resources": {
            "comfy_roots": resources["comfy_roots"],
            "node_dirs": resources["node_dirs"],
            "workflows": resources["workflows"],
            "model_candidate_count": len(resources["model_candidates"]),
        },
        "capabilities": per_capability,
        "updated_at": state.get("updated_at"),
        "runtime_verified": False,
    }


def candidates_for_component(db, component_id: str, relative_path: str) -> list[dict]:
    state = read_state(db)
    return [row for row in state["resources"]["model_candidates"]
            if row["component_id"] == component_id and row["relative_path"] == relative_path]
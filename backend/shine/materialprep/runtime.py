"""The locally managed Qwen-Image-2.1 runtime binding and its readiness ladder.

Mirrors ``shine/voiceops/runtime_config.py``: one JSON file under the workbench's
own ``comfyops`` directory, written atomically, digest-addressed, and owned by
the setup capability rather than by whatever happens to be installed on the
machine.

Readiness has four layers and they are not interchangeable.  Only the fourth one
may tell a user that a family can actually be generated:

1. ``NOT_CONFIGURED``      - no binding, or the bound instance is unreachable.
2. ``FILES_VERIFIED``     - every required model file is present and its bytes
                            match the pinned SHA-256.
3. ``GRAPH_VERIFIED``     - a live ``object_info`` really declares every node
                            class and every input the API graph and the parameter
                            map use.
4. ``RENDER_VERIFIED``    - a real small-sample image was produced for THAT
                            family under the current runtime fingerprint.

Evidence is bound to ``qwen_workflow.runtime_fingerprint``.  A new graph, model
file, instance or prompt template changes the fingerprint and downgrades the
family, because an old READY says nothing about the new configuration.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shine.materialprep import qwen_workflow
from shine.materialprep.image_prompts import template_evidence
from shine.models import utcnow

CONFIG_FILENAME = "image-runtime.json"

LAYER_NOT_CONFIGURED = "NOT_CONFIGURED"
LAYER_FILES_VERIFIED = "FILES_VERIFIED"
LAYER_GRAPH_VERIFIED = "GRAPH_VERIFIED"
LAYER_RENDER_VERIFIED = "RENDER_VERIFIED"
LAYERS = (LAYER_NOT_CONFIGURED, LAYER_FILES_VERIFIED, LAYER_GRAPH_VERIFIED, LAYER_RENDER_VERIFIED)

FAMILIES = ("character", "scene", "prop")


class ImageRuntimeConfig(BaseModel):
    """The bound instance and the fixed generation parameters.

    ``base_url`` must be loopback unless the operator explicitly allowed a remote
    renderer, because the same guard the H3 path uses applies here.
    """

    config_revision: int = 1
    instance_id: str = ""
    base_url: str = ""
    graph_sha256: str = ""
    parameter_map: dict[str, str] = Field(default_factory=dict)
    output_node_id: str = qwen_workflow.OUTPUT_NODE
    output_class_type: str = qwen_workflow.OUTPUT_CLASS_TYPE
    model_root: str = ""
    steps: int = qwen_workflow.DEFAULT_STEPS
    cfg: float = qwen_workflow.DEFAULT_CFG
    sampler_name: str = qwen_workflow.DEFAULT_SAMPLER
    scheduler: str = qwen_workflow.DEFAULT_SCHEDULER
    width: int = qwen_workflow.DEFAULT_RESOLUTION
    height: int = qwen_workflow.DEFAULT_RESOLUTION
    model_files: list[dict[str, Any]] = Field(default_factory=list)
    # Digests of the model files as they were last verified, with the size and
    # modification time they were verified at. The weights here are 16 GiB, so
    # re-hashing them on every readiness read would make the setup card and the
    # Worker unusable. A changed size or a touched file is re-hashed.
    verified_files: list[dict[str, Any]] = Field(default_factory=list)
    bound_at: str = ""


def config_path(settings) -> Path:
    return Path(settings.comfyops_dir) / CONFIG_FILENAME


def load_config(settings) -> ImageRuntimeConfig:
    path = config_path(settings)
    if not path.is_file():
        return ImageRuntimeConfig()
    try:
        return ImageRuntimeConfig.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        # An unreadable binding is "not configured", never a silent default that
        # could point at a different renderer.
        return ImageRuntimeConfig()


def save_config(settings, config: ImageRuntimeConfig) -> Path:
    path = config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="json")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return path


def config_digest(settings) -> str:
    path = config_path(settings)
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_model_files(config: ImageRuntimeConfig) -> dict[str, Any]:
    """Hash every required model file against its pinned digest.

    A file that is present but wrong is worse than a missing one: it would render
    something the audit does not describe.  Hashing multi-GB files is expensive,
    so a digest that was already computed for the current size and modification
    time is reused.  The reuse is reported as such: a cached record carries
    "cached": true and the time it was verified, and any change of size or
    modification time forces a real re-hash.
    """
    root = Path(config.model_root) if config.model_root else None
    entries = config.model_files or [dict(entry) for entry in qwen_workflow.MODEL_FILES]
    cached = {entry.get("filename"): entry for entry in (config.verified_files or [])}
    checked, problems = [], []
    for entry in entries:
        if not entry.get("required", True):
            continue
        filename = entry.get("filename") or ""
        record = {"role": entry.get("role"), "filename": filename, "expected_sha256": entry.get("sha256")}
        if root is None:
            problems.append({**record, "code": "IMAGE_MODEL_ROOT_NOT_CONFIGURED"})
            checked.append(record)
            continue
        path = root / (entry.get("folder") or "") / filename
        if not path.is_file():
            problems.append({**record, "code": "IMAGE_MODEL_FILE_MISSING", "path_present": False})
        else:
            stat = path.stat()
            if entry.get("bytes") and stat.st_size != entry["bytes"]:
                problems.append({**record, "code": "IMAGE_MODEL_SIZE_MISMATCH", "actual_bytes": stat.st_size})
            else:
                previous = cached.get(filename)
                reusable = (
                    previous
                    and previous.get("sha256")
                    and previous.get("size") == stat.st_size
                    and previous.get("mtime_ns") == stat.st_mtime_ns
                )
                if reusable:
                    record["actual_sha256"] = previous["sha256"]
                    record["cached"] = True
                    record["verified_at"] = previous.get("verified_at")
                else:
                    digest = hashlib.sha256()
                    with path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    record["actual_sha256"] = digest.hexdigest()
                    record["cached"] = False
                    record["verified_at"] = utcnow().isoformat()
                    record["size"] = stat.st_size
                    record["mtime_ns"] = stat.st_mtime_ns
                if entry.get("sha256") and record["actual_sha256"] != entry["sha256"]:
                    problems.append({**record, "code": "IMAGE_MODEL_HASH_MISMATCH"})
        checked.append(record)
    return {"files": checked, "problems": problems, "complete": not problems}


def persist_verification(settings, config: ImageRuntimeConfig, result: dict[str, Any]) -> None:
    """Store the digests that were just computed, so the next read is cheap.

    Only a real hash is stored.  A cached record keeps the size and modification
    time it was taken at, so a changed file is detected and re-hashed instead of
    being trusted.
    """
    fresh = [
        {
            "filename": record.get("filename"),
            "sha256": record.get("actual_sha256"),
            "size": record.get("size"),
            "mtime_ns": record.get("mtime_ns"),
            "verified_at": record.get("verified_at"),
        }
        for record in (result.get("files") or [])
        if record.get("actual_sha256") and not record.get("cached")
    ]
    if not fresh:
        return
    merged = {entry.get("filename"): entry for entry in (config.verified_files or [])}
    for entry in fresh:
        merged[entry["filename"]] = entry
    config.verified_files = list(merged.values())
    save_config(settings, config)


def verify_graph_against_object_info(object_info: dict[str, Any]) -> dict[str, Any]:
    """Prove the pinned graph and parameter map against a live renderer."""
    from shine.comfy.workflow import validate_against_object_info, validate_graph

    graph = qwen_workflow.base_graph()
    validate_graph(graph, required_nodes=qwen_workflow.REQUIRED_NODES)
    report = validate_against_object_info(graph, qwen_workflow.PARAMETER_MAP, object_info)
    output_spec = (object_info.get(qwen_workflow.OUTPUT_CLASS_TYPE) or {})
    report["output_node_class_present"] = bool(output_spec)
    report["complete"] = not report["missing_nodes"] and not report["missing_inputs"] and report[
        "output_node_class_present"
    ]
    return report


def current_fingerprint(config: ImageRuntimeConfig, *, graph_report: dict[str, Any] | None = None) -> str:
    """The identity readiness evidence is bound to."""
    return qwen_workflow.runtime_fingerprint(
        graph_sha256=config.graph_sha256 or qwen_workflow.BASE_GRAPH_SHA256,
        model_files=[
            {
                "role": entry.get("role"),
                "filename": entry.get("filename"),
                "bytes": entry.get("bytes"),
                "sha256": entry.get("actual_sha256") or entry.get("sha256"),
            }
            for entry in (config.model_files or [dict(e) for e in qwen_workflow.MODEL_FILES])
            if entry.get("required", True)
        ],
        instance_identity={
            "instance_id": config.instance_id,
            "base_url": config.base_url,
            "graph_verified": bool((graph_report or {}).get("complete")),
        },
        prompt_template_version=template_evidence()["template_version"],
    )


def render_verified_families(db, fingerprint: str) -> dict[str, str]:
    """Families with a real successful sample under this exact fingerprint.

    The evidence is the ledger itself: a SUCCEEDED image attempt whose recorded
    runtime fingerprint still matches, whose candidate is not a mock, whose run
    was a validation run, and whose renderer was one this installation owns.
    A stand-in renderer may prove that the chain works; its bytes are never
    counted as real verification.
    """
    from sqlalchemy import select

    from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun

    verified: dict[str, str] = {}
    attempts = db.scalars(
        select(MaterialImageAttempt).where(
            MaterialImageAttempt.status == "SUCCEEDED",
            MaterialImageAttempt.runtime_fingerprint == fingerprint,
        )
    ).all()
    for attempt in attempts:
        if not attempt.candidate_id:
            continue
        if (attempt.payload or {}).get("renderer_owned") is not True:
            continue
        item = db.get(MaterialItem, attempt.item_id)
        run = db.get(MaterialRun, attempt.run_id)
        if item is None or run is None:
            continue
        if (run.payload or {}).get("kind") != "validation":
            continue
        family = (item.payload or {}).get("family")
        if family in FAMILIES and family not in verified:
            verified[family] = attempt.id
    return verified


def _renderer_state(db, settings, instance_id: str) -> dict[str, Any]:
    """The state of the managed renderer that would serve this runtime.

    The renderer is the workbench's own process: it is started here, proved to
    be ours, and released again by the same path that closes the rest of the
    local backends.
    """
    if not instance_id:
        return {"state": "NOT_BOUND", "instance_id": "", "port": None, "pid": None}
    from shine.comfyops.image_renderer import image_renderer_state

    return image_renderer_state(db, settings, instance_id)


def readiness(db, settings, *, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """The four-layer report the setup card and the generate gate both read."""
    config = load_config(settings)
    configured = bool(config.base_url and config.instance_id)
    files = verify_model_files(config) if config.model_root else {"files": [], "problems": [], "complete": False}
    if config.model_root:
        # Store what was just hashed, so the next read does not hash 16 GiB again.
        persist_verification(settings, config, files)
    graph_report = verify_graph_against_object_info(object_info) if object_info else None
    fingerprint = current_fingerprint(config, graph_report=graph_report)

    layer = LAYER_NOT_CONFIGURED
    blockers: list[dict[str, Any]] = []
    if not configured:
        blockers.append({"code": "IMAGE_RUNTIME_NOT_BOUND", "layer": LAYER_NOT_CONFIGURED})
    elif not files["complete"]:
        layer = LAYER_NOT_CONFIGURED
        blockers.extend(files["problems"])
    else:
        layer = LAYER_FILES_VERIFIED
        if graph_report is None:
            blockers.append({"code": "IMAGE_GRAPH_NOT_VERIFIED", "layer": LAYER_GRAPH_VERIFIED})
        elif not graph_report["complete"]:
            blockers.append({"code": "IMAGE_GRAPH_NOT_VERIFIED", **graph_report})
        else:
            layer = LAYER_GRAPH_VERIFIED

    verified = render_verified_families(db, fingerprint) if layer == LAYER_GRAPH_VERIFIED else {}
    renderer = _renderer_state(db, settings, config.instance_id)
    families = {
        family: {
            "layer": LAYER_RENDER_VERIFIED if family in verified else layer,
            "evidence_attempt_id": verified.get(family),
            "can_generate": family in verified,
        }
        for family in FAMILIES
    }
    return {
        "configured": configured,
        "layer": layer,
        "families": families,
        "renderer": renderer,
        "fingerprint": fingerprint,
        "config_revision": config.config_revision,
        "config_digest": config_digest(settings),
        "graph_sha256": config.graph_sha256 or qwen_workflow.BASE_GRAPH_SHA256,
        "output_node_id": config.output_node_id,
        "output_class_type": config.output_class_type,
        "model_files": files["files"],
        "graph_report": graph_report,
        "prompt_template": template_evidence(),
        "blockers": blockers,
        "license": {
            "name": "Qwen RESEARCH LICENSE AGREEMENT",
            "release_date": "2026-09-20",
            "scope": "NON_COMMERCIAL_RESEARCH_OR_EVALUATION_ONLY",
            "note": (
                "第 1.i 条将非商业用途限定为研究或评估；第 2.b 条要求商业使用另行取得许可。"
                "素材晋升不产生任何商业授权。"
            ),
        },
        "upstream": {
            "template_repository": qwen_workflow.TEMPLATE_REPOSITORY,
            "template_commit": qwen_workflow.TEMPLATE_COMMIT,
            "template_sha256": qwen_workflow.TEMPLATE_SHA256,
            "model_repository": qwen_workflow.MODEL_REPOSITORY,
            "model_revision": qwen_workflow.MODEL_REVISION,
            "required_bytes": qwen_workflow.REQUIRED_MODEL_BYTES,
        },
    }

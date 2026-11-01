"""Operator-supplied recipes for the one managed renderer and review services.

A recipe is configuration, not authority. It names the executables, the
ComfyUI root, the pinned adapter bytes and the physical thresholds. The handoff
control tokens are generated per start and never persisted, and a recipe never
authorizes a job, a lease, a review reservation or a submission.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from fastapi import HTTPException

from shine.comfyops.storage import prepare_process_environment

PLACEHOLDERS = {"python", "bootstrap", "base_directory", "root", "adapter_dir", "port",
                "manifest", "runtime_packages"}
RENDERER_KEYS = ("instance_id", "python", "root", "base_directory", "bootstrap", "adapter_dir",
                 "adapter_sha256", "gpu_uuid", "argv", "pinned_files")
REVIEW_KEYS = ("service_id", "python", "manifest", "runtime_packages", "port", "argv")


def adapter_identity(adapter_dir) -> str:
    """The adapter's own identity, as the ComfyUI plugin computes it."""
    directory = Path(adapter_dir)
    parts = []
    for name in ("__init__.py", "barrier.py"):
        parts.append(hashlib.sha256((directory / name).read_bytes()).digest())
    return hashlib.sha256(b"".join(parts)).hexdigest()


def _absolute(value):
    if not isinstance(value, str) or not value:
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    path = Path(value)
    if not path.is_absolute():
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    if not path.exists():
        raise HTTPException(409, "COMFY_RECIPE_PATH_MISSING")
    return str(path)


def _expand(template, values):
    def replace(match):
        name = match.group(1)
        if values.get(name) is None:
            raise HTTPException(409, "COMFY_RECIPE_INVALID")
        return str(values[name])

    return re.sub(r"\{([a-z_]+)\}", replace, template)


def _check_templates(templates):
    for template in templates:
        if not isinstance(template, str):
            raise HTTPException(409, "COMFY_RECIPE_INVALID")
        for name in re.findall(r"\{([a-z_]+)\}", template):
            if name not in PLACEHOLDERS:
                raise HTTPException(409, "COMFY_RECIPE_INVALID")


def _renderer(raw):
    if not isinstance(raw, dict) or any(key not in raw for key in RENDERER_KEYS):
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    recipe = {
        "instance_id": raw["instance_id"],
        "python": _absolute(raw["python"]),
        "root": _absolute(raw["root"]),
        "base_directory": _absolute(raw["base_directory"]),
        "bootstrap": _absolute(raw["bootstrap"]),
        "adapter_dir": _absolute(raw["adapter_dir"]),
        "adapter_sha256": raw["adapter_sha256"],
        "gpu_uuid": raw["gpu_uuid"],
        "min_ram_mb": int(raw.get("min_ram_mb", 8192)),
        "min_vram_mb": int(raw.get("min_vram_mb", 12000)),
        "argv": list(raw["argv"]),
        "pinned_files": list(raw["pinned_files"]),
        "port": int(raw["port"]) if raw.get("port") else None,
    }
    if (not isinstance(recipe["instance_id"], str) or not recipe["instance_id"]
            or not re.fullmatch(r"[a-f0-9]{64}", str(recipe["adapter_sha256"]))
            or not isinstance(recipe["gpu_uuid"], str) or not recipe["gpu_uuid"]
            or recipe["min_ram_mb"] <= 0 or recipe["min_vram_mb"] <= 0
            or not recipe["argv"] or not recipe["pinned_files"]):
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    if adapter_identity(recipe["adapter_dir"]) != recipe["adapter_sha256"]:
        raise HTTPException(409, "COMFY_RECIPE_ADAPTER_CHANGED")
    _check_templates(list(recipe["argv"]) + list(recipe["pinned_files"]))
    return recipe


def _review(raw, kind):
    if not isinstance(raw, dict) or any(key not in raw for key in REVIEW_KEYS):
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    recipe = {
        "kind": kind,
        "service_id": raw["service_id"],
        "python": _absolute(raw["python"]),
        "manifest": _absolute(raw["manifest"]),
        "runtime_packages": _absolute(raw["runtime_packages"]),
        "port": int(raw["port"]),
        "argv": list(raw["argv"]),
        "min_ram_mb": int(raw.get("min_ram_mb", 8000)),
        "min_vram_mb": int(raw.get("min_vram_mb", 8000)),
        "runtime_sha256": raw.get("runtime_sha256"),
    }
    if (not isinstance(recipe["service_id"], str) or not recipe["service_id"]
            or not 0 < recipe["port"] < 65536
            or recipe["min_ram_mb"] <= 0 or recipe["min_vram_mb"] <= 0
            or not recipe["argv"]):
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    _check_templates(list(recipe["argv"]))
    return recipe


def load_managed_recipe(path, *, expected_sha256=None):
    """Read and validate the managed renderer and review recipes."""
    target = Path(path)
    if not target.is_absolute() or not target.is_file():
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "COMFY_RECIPE_INVALID") from exc
    if not isinstance(raw, dict) or "renderer" not in raw:
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    if expected_sha256:
        from shine.comfy.workflow import graph_hash
        if graph_hash(raw) != expected_sha256:
            raise HTTPException(409, 'ACTIVE_RUNTIME_RECIPE_CHANGED')
    reviews = raw.get("reviews") or {}
    if not isinstance(reviews, dict) or any(kind not in {"VLM", "ASR"} for kind in reviews):
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    return {
        "renderer": _renderer(raw["renderer"]),
        "reviews": {kind: _review(spec, kind) for kind, spec in reviews.items()},
    }


def build_renderer_launch(recipe, *, port=None, token=None):
    """Materialise the renderer argv, environment and pinned files for one start."""
    token = token or secrets.token_urlsafe(32)
    port = port if port is not None else recipe.get("port")
    if not isinstance(port, int) or not 0 < port < 65536:
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    values = {"python": recipe["python"], "bootstrap": recipe["bootstrap"],
              "base_directory": recipe["base_directory"], "root": recipe["root"],
              "adapter_dir": recipe["adapter_dir"], "port": str(port)}
    argv = [_expand(item, values) for item in recipe["argv"]]
    pins = [_expand(item, values) for item in recipe["pinned_files"]]
    if not argv or not Path(argv[0]).is_absolute():
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    for pin in pins:
        if not Path(pin).is_absolute() or not Path(pin).is_file():
            raise HTTPException(409, "COMFY_RECIPE_INVALID")
    if str(Path(argv[0]).resolve()) not in {str(Path(pin).resolve()) for pin in pins}:
        raise HTTPException(409, "COMFY_RECIPE_EXECUTABLE_NOT_PINNED")
    env = {**os.environ, **prepare_process_environment(recipe['root'])}
    env.update({
        "SHINE_COMFY_HANDOFF_TOKEN": token,
        "SHINE_COMFY_START_DRAINED": "1",
        "SHINE_COMFY_BOOTSTRAP_ROOT": recipe["root"],
        "SHINE_COMFY_ADAPTER_FILE": str(Path(recipe["adapter_dir"]) / "__init__.py"),
        "SHINE_COMFY_EXPECTED_ADAPTER_SHA256": recipe["adapter_sha256"],
    })
    return argv, recipe["root"], env, pins, token, port


def build_review_launch(recipe, *, token=None):
    """Materialise the isolated review supervisor launch for one start."""
    token = token or secrets.token_urlsafe(32)
    values = {"python": recipe["python"], "manifest": recipe["manifest"],
              "runtime_packages": recipe["runtime_packages"], "port": str(recipe["port"])}
    argv = [_expand(item, values) for item in recipe["argv"]]
    pins = [recipe["python"], recipe["manifest"]]
    if not argv or not Path(argv[0]).is_absolute():
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    if str(Path(argv[0]).resolve()) not in {str(Path(pin).resolve()) for pin in pins}:
        raise HTTPException(409, "COMFY_RECIPE_EXECUTABLE_NOT_PINNED")
    # The working directory must be a directory: the interpreter itself is the
    # executable, and its parent is the only sensible working directory.
    cwd = str(Path(recipe["python"]).resolve().parent)
    if not Path(cwd).is_dir():
        raise HTTPException(409, "COMFY_RECIPE_INVALID")
    env = {**os.environ, **prepare_process_environment(recipe['runtime_packages'])}
    env["SHINE_REVIEW_HANDOFF_TOKEN"] = token
    return argv, cwd, env, pins, token

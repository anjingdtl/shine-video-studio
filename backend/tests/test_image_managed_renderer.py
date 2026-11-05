"""The managed renderer that serves the image runtime.

These tests cover the decisions that are made before a process exists: which
recipe may be used, which instance may be bound, and what the state report
says when nothing runs.  The start itself is proved on a real machine; a fake
process group would only prove the fake.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from shine.comfyops import image_renderer
from shine.comfyops.models import ComfyInstance
from shine.models import AppSetting


def _write_recipe(settings, tmp_path, *, instance_id="managed-instance", port=8188):
    """A recipe that points at the repository's own pinned adapter."""
    from shine.comfyops.deployment import ADAPTER_DIR
    from shine.comfyops.managed_recipe import adapter_identity

    root = tmp_path / "comfy-root"
    root.mkdir(parents=True, exist_ok=True)
    bootstrap = Path("scripts/managed_comfy_bootstrap.py").resolve()
    assert bootstrap.is_file()
    recipe = {
        "renderer": {
            "instance_id": instance_id,
            "python": str(Path(sys.executable).resolve()),
            "root": str(root),
            "base_directory": str(root),
            "runtime_data_root": str(root),
            "bootstrap": str(bootstrap),
            "adapter_dir": str(ADAPTER_DIR),
            "adapter_sha256": adapter_identity(ADAPTER_DIR),
            "gpu_uuid": "GPU-TEST",
            "port": port,
            "argv": ["{python}", "{bootstrap}", "--listen", "127.0.0.1", "--port", "{port}"],
            "pinned_files": ["{python}", "{bootstrap}"],
        },
        "reviews": {},
    }
    path = image_renderer.recipe_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recipe), encoding="utf-8")
    return path


def test_the_recipe_is_required_before_anything_else(api, tmp_path):
    """A missing recipe is a named blocker, never an invented renderer."""
    _, _, settings = api
    with pytest.raises(HTTPException) as failure:
        image_renderer.load_image_recipe(settings)
    assert failure.value.detail["code"] == "IMAGE_RENDERER_RECIPE_MISSING"


def test_the_bound_instance_has_to_match_the_recipe(api, tmp_path):
    """The image runtime may only use the instance the recipe describes."""
    _, factory, settings = api
    _write_recipe(settings, tmp_path, instance_id="managed-instance")
    with pytest.raises(HTTPException) as failure:
        image_renderer.start_image_renderer(factory, settings, instance_id="other-instance", actor="local-user")
    assert failure.value.detail["code"] == "IMAGE_RENDERER_RECIPE_MISMATCH"


def test_an_unmanaged_instance_is_refused(api, tmp_path):
    """A renderer the workbench cannot own may not serve the image runtime."""
    _, factory, settings = api
    _write_recipe(settings, tmp_path, instance_id="managed-instance")
    with factory() as db:
        db.add(ComfyInstance(
            id="managed-instance",
            payload={"managed": False, "is_mock": False},
            sha256="0" * 64,
            status="STOPPED",
        ))
        db.commit()
    with pytest.raises(HTTPException) as failure:
        image_renderer.start_image_renderer(factory, settings, instance_id="managed-instance", actor="local-user")
    assert failure.value.detail == "IMAGE_RENDERER_MANAGED_INSTANCE_REQUIRED"


def test_the_state_report_is_honest_when_nothing_runs(api, tmp_path):
    """No startup record means no renderer; the report never invents one."""
    _, factory, settings = api
    with factory() as db:
        assert image_renderer.image_renderer_state(db, settings, "")["state"] == "NOT_BOUND"
        assert image_renderer.image_renderer_state(db, settings, "managed-instance")["state"] == "NOT_STARTED"


def test_a_running_renderer_is_reused_and_never_restarted(api, tmp_path, monkeypatch):
    """A renderer this installation already owns is reused as it is."""
    _, factory, settings = api
    monkeypatch.setattr(
        image_renderer, "managed_process_state", lambda record: "RUNNING"
    )
    with factory() as db:
        db.add(AppSetting(
            key=image_renderer.image_startup_key("managed-instance"),
            value={"stage": "RUNNING", "port": 8188},
        ))
        db.commit()
    result = image_renderer.ensure_image_renderer(factory, settings, instance_id="managed-instance")
    assert result["status"] == "RUNNING"
    assert result["reused"] is True


def test_the_startup_key_is_bounded(api):
    """The ledger key may not grow without an end."""
    with pytest.raises(HTTPException):
        image_renderer.image_startup_key("x" * 200)

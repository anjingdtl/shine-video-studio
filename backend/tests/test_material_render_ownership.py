"""The fourth readiness layer belongs to a renderer this installation owns.

The renderer in this file is still the protocol stand-in: what is under test is
the ownership rule, not the hardware.  A managed instance plus a renderer the
workbench started turns a successful sample into real verification; a foreign
renderer never does, no matter how good its bytes are.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ComfyInstance, PreProductionPlan, RightsRecord
from shine.materialprep import execution, runtime
from shine.materialprep.models import MaterialImageAttempt, MaterialItem, MaterialRun
from tests.test_material_image_jobs import object_info_fixture
from tests.test_material_render_e2e import FakeRenderer


def _prepare(factory, settings, project_id, base_url, models, *, idempotency_key):
    weight = models / "diffusion_models" / "qwen.safetensors"
    config = runtime.ImageRuntimeConfig(
        config_revision=1,
        instance_id="managed-instance",
        base_url=base_url,
        model_root=str(models),
        model_files=[{
            "role": "unet",
            "folder": "diffusion_models",
            "filename": "qwen.safetensors",
            "bytes": weight.stat().st_size,
            "sha256": hashlib.sha256(weight.read_bytes()).hexdigest(),
            "required": True,
        }],
    )
    runtime.save_config(settings, config)
    with factory() as db:
        instance_payload = {"managed": True, "is_mock": False, "name": "managed", "port": 8188}
        db.add(ComfyInstance(
            id="managed-instance",
            payload=instance_payload,
            sha256=graph_hash(instance_payload),
            status="STOPPED",
        ))
        plan_payload = {
            "purpose": "asset_preparation",
            "document_id": "doc",
            "script_hash": "h",
            "scene_preparations": [],
            "requirements": [],
            "visual_direction": None,
            "confirmation": {"confirmation_id": "c" * 64, "confirmation_hash": "d" * 64},
            "manifest": None,
        }
        plan = PreProductionPlan(
            project_id=project_id,
            payload=plan_payload,
            sha256=graph_hash(plan_payload),
            status="CONFIRMED",
            purpose="asset_preparation",
        )
        db.add(plan)
        db.flush()
        rights_payload = {"scope": "asset", "license_id": "local-research", "purpose": "material preparation"}
        rights = RightsRecord(
            project_id=project_id,
            status="APPROVED",
            payload=rights_payload,
            sha256=graph_hash(rights_payload),
        )
        db.add(rights)
        db.flush()
        run_payload = {"kind": "validation", "rights_id": rights.id}
        run = MaterialRun(
            project_id=project_id,
            plan_id=plan.id,
            confirmation_id="c" * 64,
            confirmation_hash="d" * 64,
            idempotency_key=idempotency_key,
            request_hash="rq",
            mode="real",
            status="RUNNING",
            payload=run_payload,
            sha256=graph_hash(run_payload),
        )
        db.add(run)
        db.flush()
        item_payload = {
            "family": "character",
            "entity_id": "林知远",
            "brief": {
                "schema_version": "1.0",
                "appearance": "a man in his thirties with short black hair, wearing a long dark wool coat",
                "must_keep": [],
                "avoid": [],
                "questions": [],
            },
            "visual_direction": {},
            "seed": 7,
            "kind": "validation",
        }
        item = MaterialItem(
            run_id=run.id,
            project_id=project_id,
            requirement_id=None,
            generation_revision=1,
            status="QUEUED",
            payload=item_payload,
            sha256=graph_hash(item_payload),
        )
        db.add(item)
        db.flush()
        item_id = item.id
        db.commit()
    return item_id


def test_an_owned_renderer_grants_the_fourth_readiness_layer(api, tmp_path, monkeypatch):
    """A sample rendered by the workbench's own renderer is real verification."""
    client, factory, settings = api
    project_id = client.post("/api/v1/projects", json={"title": "出图所有权"}).json()["id"]
    models = tmp_path / "models"
    (models / "diffusion_models").mkdir(parents=True)
    (models / "diffusion_models" / "qwen.safetensors").write_bytes(b"pinned-weight-bytes")

    with FakeRenderer() as base_url:
        item_id = _prepare(factory, settings, project_id, base_url, models, idempotency_key="owned-render")
        # The managed renderer is already up in this fiction, so the start is a
        # no-op; the ownership rule is what the test is about.
        monkeypatch.setattr(
            "shine.comfyops.image_renderer.ensure_image_renderer",
            lambda *args, **kwargs: {"status": "RUNNING", "reused": True},
        )

        result = execution.execute_item(factory, settings, item_id, "worker-1")

        assert result["status"] == "CANDIDATE_READY", result
        with factory() as db:
            attempt = db.scalar(select(MaterialImageAttempt).where(MaterialImageAttempt.item_id == item_id))
            assert attempt.payload["renderer_owned"] is True
            report = runtime.readiness(db, settings, object_info=object_info_fixture())
        assert report["families"]["character"]["can_generate"] is True, report


def test_a_flaky_graph_probe_cannot_rewrite_the_runtime_fingerprint(tmp_path):
    """Identity is configuration; a probe result is readiness, not identity.

    The fingerprint used to include whether the object_info probe had just
    succeeded.  One unreachable moment therefore rewrote it and silently
    downgraded every family that had already rendered - a validation that
    evaporated by being looked at.
    """
    models = tmp_path / "models"
    (models / "diffusion_models").mkdir(parents=True)
    weight = models / "diffusion_models" / "qwen.safetensors"
    weight.write_bytes(b"pinned-weight-bytes")
    config = runtime.ImageRuntimeConfig(
        instance_id="managed-instance",
        base_url="http://127.0.0.1:8188",
        model_root=str(models),
        model_files=[{
            "role": "unet",
            "folder": "diffusion_models",
            "filename": "qwen.safetensors",
            "bytes": weight.stat().st_size,
            "sha256": hashlib.sha256(weight.read_bytes()).hexdigest(),
            "required": True,
        }],
    )
    probed = runtime.current_fingerprint(config, graph_report={"complete": True})
    unreachable = runtime.current_fingerprint(config, graph_report=None)
    assert probed == unreachable

"""External ComfyUI adoption: verified bytes must become *consumable*.

The RTX 4070 audit had 43.97 GB of H3 weights verified by SHA-256 and still
reported ``bound=false`` / ``consumable=false``.  The missing link was the
binding evidence: an external installation keeps weights in the user's own
``models/<family>`` tree, not in Shine's isolated package store, and the old
readiness code only understood the managed store.

These tests prove the new contract without copying or downloading a byte:
a path-bound receipt inside the adopted instance's family directory is bound;
the same receipt against a different installation is not.
"""

from __future__ import annotations

import hashlib

import pytest

from shine.comfy.workflow import graph_hash
from shine.comfyops.models import ComfyInstance
from shine.comfyops.variant_binding import VariantBinding
from shine.comfyops.variant_readiness import current_instance, weight_evidence
from shine.comfyops.verification_receipts import save_receipt
from tests.conftest import create_project

pytestmark = pytest.mark.integration

PAYLOAD = b"H3 synthetic weight bytes for a binding contract test"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def comfy_root(tmp_path, name="ComfyUI"):
    root = tmp_path / name
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("raise RuntimeError('never execute')")
    (root / "models" / "unet").mkdir(parents=True)
    weight = root / "models" / "unet" / "h3.safetensors"
    weight.write_bytes(PAYLOAD)
    return root, weight


def binding() -> VariantBinding:
    component = {
        "component_id": "minimax-h3-unet",
        "kind": "model",
        "status": "READY",
        "destination_family": "diffusion_models",
        "revision": "r1",
        "files": [{
            "relative_path": "h3.safetensors",
            "sha256": SHA,
            "size": len(PAYLOAD),
        }],
    }
    profile = {"name": "p", "graph": {"1": {"inputs": {"unet_name": "h3.safetensors"}}}}
    return VariantBinding(
        variant_id="ref2va-int8-convrot",
        catalog_id="shine-h3-ref2va-windows-x64",
        catalog={"catalog_id": "shine-h3-ref2va-windows-x64", "catalog_version": "1",
                 "components": [component]},
        manifest={"acceptance_status": "SUPPORTED"},
        profiles=(profile,),
    )


def adopt(db, root, *, name="adopted"):
    payload = {"managed": True, "adopted": True, "external_installation": True,
               "root": str(root), "name": name, "port": 8188}
    row = ComfyInstance(id="local-8188", payload=payload, sha256=graph_hash(payload),
                        status="STOPPED")
    db.add(row)
    db.flush()
    return row


def record_receipt(db, root, weight):
    from shine.comfyops.existing_installation import declared_model_family_roots

    allowed = [root, *[directory for dirs in declared_model_family_roots(root).values()
                       for directory in dirs]]
    save_receipt(
        db,
        variant_id="ref2va-int8-convrot",
        catalog_id="shine-h3-ref2va-windows-x64",
        catalog_version="1",
        component=binding().catalog["components"][0],
        root=root,
        directory=root,
        file_paths={"h3.safetensors": weight},
        allowed_roots=allowed,
    )


def test_verified_files_inside_the_adopted_installation_are_consumable(api, tmp_path):
    client, factory, settings = api
    create_project(client)
    root, weight = comfy_root(tmp_path)
    with factory.begin() as db:
        adopt(db, root)
        record_receipt(db, root, weight)

    with factory() as db:
        evidence = weight_evidence(db, settings, binding())

    assert len(evidence) == 1
    row = evidence[0]
    assert row["prepared"] is True
    assert row["verified"] is True
    assert row["bound_to_instance"] is True
    assert row["consumable"] is True
    assert row["binding_reason"] == "ADOPTED_INSTALLATION_FAMILY_DIRECTORY"
    assert row["receipt"]["paths_verified"] is True


def test_the_same_receipt_is_not_bound_to_a_different_installation(api, tmp_path):
    client, factory, settings = api
    create_project(client)
    source_root, weight = comfy_root(tmp_path, "SourceComfy")
    other_root, _ = comfy_root(tmp_path, "OtherComfy")
    with factory.begin() as db:
        adopt(db, other_root)
        record_receipt(db, source_root, weight)

    with factory() as db:
        evidence = weight_evidence(db, settings, binding())

    row = evidence[0]
    assert row["verified"] is True
    # Verified is a fact about the bytes; bound is a fact about the instance.
    assert row["bound_to_instance"] is False
    assert row["consumable"] is False
    assert row["binding_reason"] == "VERIFIED_ELSEWHERE_NOT_BOUND"


def test_weights_declared_outside_the_checkout_are_bound_by_the_declaration(api, tmp_path):
    client, factory, settings = api
    create_project(client)
    root, _ = comfy_root(tmp_path)
    external = tmp_path / "weights" / "unet"
    external.mkdir(parents=True)
    weight = external / "h3.safetensors"
    weight.write_bytes(PAYLOAD)
    (root / "extra_model_paths.yaml").write_text(
        "shared:\n"
        f"    base_path: {tmp_path / 'weights'}\n"
        "    unet: unet\n",
        encoding="utf-8",
    )
    with factory.begin() as db:
        adopt(db, root)
        record_receipt(db, root, weight)

    with factory() as db:
        info = current_instance(db, settings)
        evidence = weight_evidence(db, settings, binding())

    assert info["mode"] == "ADOPTED_EXTERNAL_INSTALLATION"
    assert any(str(external) in row for row in info["families"].get("unet", []))
    assert evidence[0]["consumable"] is True


def test_no_instance_means_no_binding(api, tmp_path):
    client, factory, settings = api
    create_project(client)
    root, weight = comfy_root(tmp_path)
    with factory.begin() as db:
        record_receipt(db, root, weight)

    with factory() as db:
        info = current_instance(db, settings)
        evidence = weight_evidence(db, settings, binding())

    assert info["mode"] == "NONE"
    assert evidence[0]["verified"] is True
    assert evidence[0]["consumable"] is False

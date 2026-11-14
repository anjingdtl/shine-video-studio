"""R02: the production gate reads verified bytes, not discovered bytes.

The gate used to ask whether the files were *present*.  A cache file whose
name is the pinned digest and whose bytes are wrong therefore made a
candidate selectable: the readiness report said "prepared", and the gate said
"selectable, no blockers".  The gate now reads the verification itself and
the deployment relation: the bytes must be hash-verified, and the renderer
that would execute them must read them from that very place.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shine.comfyops import verification_receipts as receipts
from shine.comfyops.models import ComfyInstance
from tests.test_variant_binding_and_gate import (
    LOWEND_VARIANT,
    _fake_managed_runtime,
    _gate,
    _mark_validated,
    _register_candidate,
    _studio,
)

PAYLOAD = b"0123456789"
WRONG = b"9876543210"
INSTANCE_ID = "local:http://127.0.0.1:8188"


def _component(component_id: str = "synthetic-weights") -> dict:
    return {
        "component_id": component_id,
        "revision": "rev-1",
        "files": [{"relative_path": "weights.bin",
                   "sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                   "size": len(PAYLOAD)}],
    }


def _synthetic_weights(monkeypatch, component: dict) -> None:
    monkeypatch.setattr(
        "shine.comfyops.variant_readiness._required_components",
        lambda catalog, profiles: [component],
    )


def _cache_dir(settings) -> Path:
    directory = settings.comfyops_dir / "isolated-packages" / ".cache"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _installed_dir(settings, component: dict) -> Path:
    directory = settings.comfyops_dir / "isolated-packages" / component["component_id"]
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_weights(directory: Path, payload: bytes = PAYLOAD) -> Path:
    target = directory / "weights.bin"
    target.write_bytes(payload)
    return target


def _save_receipt(factory, component: dict, directory: Path) -> None:
    with factory.begin() as db:
        receipts.save_receipt(
            db, variant_id=LOWEND_VARIANT, catalog_id=None, catalog_version=None,
            component=component, root=directory.parent, directory=directory,
        )


def _ready(client, factory, monkeypatch, settings, project: str, profile_id: str) -> dict:
    """A candidate whose profile is validated and whose runtime is configured."""
    _mark_validated(factory, profile_id)
    _fake_managed_runtime(monkeypatch)
    return _gate(client, project, profile_id)


def test_a_cache_file_with_wrong_bytes_is_not_selectable(api, monkeypatch, fake_deployment_host):
    """The counterexample: same length, wrong bytes, and the gate said yes."""
    client, factory, settings = api
    studio = _studio(client)
    project = studio["project"]
    row = _register_candidate(client, project)
    component = _component()
    _synthetic_weights(monkeypatch, component)

    # A cache file named after the pinned digest, carrying the wrong bytes.
    cache = _cache_dir(settings)
    (cache / component["files"][0]["sha256"]).write_bytes(WRONG)

    report = client.get(
        f"/api/v1/setup/variant-readiness?project_id={project}&variant_id={LOWEND_VARIANT}").json()
    evidence = next(row for row in report["weights"]
                    if row["component_id"] == "synthetic-weights")
    assert evidence["prepared"] is True, "the bytes were found"
    assert evidence["verified"] is False, "and never checked"

    gate = _ready(client, factory, monkeypatch, settings, project, row["profile_id"])
    codes = {blocker["code"] for blocker in gate["blockers"]}
    assert gate["selectable"] is False, gate["blockers"]
    assert "PROFILE_WEIGHTS_NOT_VERIFIED" in codes, codes


def test_a_valid_in_place_verification_is_selectable(api, monkeypatch):
    """The positive: the bytes verified where the component belongs."""
    client, factory, settings = api
    studio = _studio(client)
    project = studio["project"]
    row = _register_candidate(client, project)
    component = _component()
    _synthetic_weights(monkeypatch, component)

    directory = _installed_dir(settings, component)
    _write_weights(directory)
    _save_receipt(factory, component, directory)

    gate = _ready(client, factory, monkeypatch, settings, project, row["profile_id"])
    assert gate["selectable"] is True, gate["blockers"]
    assert gate["blockers"] == []


def test_verified_bytes_in_another_directory_are_not_consumable(api, monkeypatch, tmp_path):
    """Only another directory holds them: the renderer would not read it."""
    client, factory, settings = api
    studio = _studio(client)
    project = studio["project"]
    row = _register_candidate(client, project)
    component = _component()
    _synthetic_weights(monkeypatch, component)

    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir(parents=True)
    _write_weights(elsewhere)
    _save_receipt(factory, component, elsewhere)

    gate = _ready(client, factory, monkeypatch, settings, project, row["profile_id"])
    codes = {blocker["code"] for blocker in gate["blockers"]}
    assert gate["selectable"] is False, gate["blockers"]
    assert "PROFILE_WEIGHTS_NOT_CONSUMABLE" in codes, codes


def test_verified_bytes_the_current_instance_binds_are_consumable(api, monkeypatch, tmp_path):
    """A bound directory is a deployment relation the gate accepts."""
    client, factory, settings = api
    studio = _studio(client)
    project = studio["project"]
    row = _register_candidate(client, project)
    component = _component()
    _synthetic_weights(monkeypatch, component)

    bound = tmp_path / "bound-weights"
    bound.mkdir(parents=True)
    _write_weights(bound)
    _save_receipt(factory, component, bound)

    root = tmp_path / "instance"
    root.mkdir(parents=True)
    (root / "extra_model_paths.yaml").write_text(
        json.dumps({"shine": {"checkpoints": str(bound)}}), encoding="utf-8")
    with factory.begin() as db:
        db.add(ComfyInstance(id=INSTANCE_ID,
                             payload={"root": str(root), "managed": True},
                             sha256="0" * 64, status="RUNNING"))

    gate = _ready(client, factory, monkeypatch, settings, project, row["profile_id"])
    assert gate["selectable"] is True, gate["blockers"]


def test_the_package_entry_refuses_what_the_gate_refuses(api, monkeypatch, fake_deployment_host):
    """The same verdict on the way into a director package.

    ``fake_deployment_host`` keeps the deployment-storage drive policy out of
    the way: on a CI runner whose pytest temp dir lives on the system drive,
    ``require_storage`` would otherwise refuse to read the cache at all, and
    the wrong-bytes file this test planted would never be seen - turning the
    byte-verification verdict (NOT_VERIFIED) into a storage-policy verdict
    (NOT_AVAILABLE).  The sibling wrong-bytes test already isolates this.
    """
    client, factory, settings = api
    studio = _studio(client)
    project = studio["project"]
    row = _register_candidate(client, project)
    component = _component()
    _synthetic_weights(monkeypatch, component)
    cache = _cache_dir(settings)
    (cache / component["files"][0]["sha256"]).write_bytes(WRONG)
    _mark_validated(factory, row["profile_id"])
    _fake_managed_runtime(monkeypatch)

    plan = client.post(f"/api/v1/projects/{project}/episode-plans",
                       json={"target_seconds": 8, "tolerance_seconds": 0},
                       headers={"X-Shine-Local-Action": "1"})
    assert plan.status_code == 201, plan.text
    confirmed = client.post(
        f"/api/v1/projects/{project}/episode-plans/{plan.json()['version']}/confirm",
        json={}, headers={"X-Shine-Local-Action": "1"})
    assert confirmed.status_code == 200, confirmed.text

    response = client.post(f"/api/v1/projects/{project}/director-packages",
                           json={"workflow_profile_id": row["profile_id"],
                                 "generation_mode": "Ref2VA"},
                           headers={"X-Shine-Local-Action": "1"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "PROFILE_WEIGHTS_NOT_VERIFIED"

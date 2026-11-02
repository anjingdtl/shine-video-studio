"""Adopt an existing ComfyUI by immutable evidence; never copy or download it."""
# ruff: noqa: F811

import shutil

from sqlalchemy import select

from shine.comfyops import adoption, deployment
from shine.comfyops.models import ComfyInstance, InstallationAttempt, InstallationPlan
from shine.services import add_audit
from tests.conftest import create_project
from tests.test_v13_preparation import HEADERS, prep  # noqa: F401


def _install_fixture(tmp_path, catalog):
    root = tmp_path / "existing" / "ComfyUI"
    (root / "comfy").mkdir(parents=True)
    (root / "main.py").write_text("# test only\n", encoding="utf-8")
    python = root.parent / "python_embeded" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"SYNTHETIC TEST INTERPRETER")
    adapter_target = root / "custom_nodes" / "shine_comfy_handoff"
    shutil.copytree(deployment.ADAPTER_DIR, adapter_target)
    (root / "custom_nodes" / "shine_h3_sampling_mode.py").write_text("# test only\n", encoding="utf-8")
    for component in catalog["components"]:
        if component.get("kind") == "node":
            node = root / "custom_nodes" / component["component_id"]
            node.mkdir(parents=True, exist_ok=True)
            (node / "__init__.py").write_text("# test only\n", encoding="utf-8")

    h3_specs = [
        (component["component_id"], component["destination_family"], item)
        for component in catalog["components"]
        if component.get("kind") == "model" and component["component_id"].startswith("minimax-h3-")
        for item in component.get("files", [])
    ]
    matches = []
    verified_files = []
    for component_id, family, item in h3_specs:
        folder = "clip" if family == "text_encoders" else family
        source = root / "models" / folder / item["relative_path"]
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"SYNTHETIC TEST MODEL FILE")
        entry = {
            "component_id": component_id,
            "relative_path": item["relative_path"],
            "sha256": item["sha256"],
            "size": item["size"],
        }
        matches.append({**entry, "sources": [str(source)], "source_stats": {str(source): {}}})
        verified_files.append(entry)
    report = {
        "root": str(root.resolve()),
        "snapshot": "a" * 64,
        "catalog_id": catalog["catalog_id"],
        "matches": matches,
        "missing": [],
        "software": {
            "installed": True,
            "version_status": "INSTALLED_VERSION_MATCH",
            "actual_revision": next(c["revision"] for c in catalog["components"] if c["component_id"] == "comfyui"),
            "runtime": {"python_executable": str(python.resolve())},
        },
    }
    return root, report, verified_files


def _seed_verification(factory, catalog, report, verified_files, *, summary_only=False):
    with factory.begin() as db:
        payload = {
            "root": "[local-path]",
            "snapshot": report["snapshot"],
            "verified_bytes": sum(item["size"] for item in verified_files),
            "copied_bytes": 0,
        }
        if summary_only:
            payload.update({
                "verified_components": [item["component_id"] for item in verified_files],
                "rejected_components": [],
            })
        else:
            payload.update({"verified_files": verified_files, "rejected_files": []})
        add_audit(db, "test-operator", "EXISTING_COMFY_INSTALLATION_VERIFIED", "deployment",
                  catalog["catalog_id"], payload)


def test_user_confirmation_adopts_verified_existing_h3_without_copy_or_download(prep, tmp_path, monkeypatch):
    client, factory, settings = prep
    project = create_project(client, "existing-Comfy adoption")
    catalog = deployment.load_default_catalog()
    root, report, verified_files = _install_fixture(tmp_path, catalog)
    _seed_verification(factory, catalog, report, verified_files)
    monkeypatch.setattr(adoption, "inspect", lambda _path, _catalog: report)
    monkeypatch.setattr("shine.comfyops.existing_installation.inspect", lambda _path, _catalog: report)

    response = client.post("/api/v1/setup/existing-installations/adopt", headers=HEADERS, json={
        "project_id": project,
        "path": str(root),
        "snapshot": report["snapshot"],
        "instance_name": "shine-h3",
        "port": 8188,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ADOPTED"
    assert response.json()["copied_bytes"] == response.json()["downloaded_bytes"] == 0
    assert response.json()["installed_h3_bytes"] == sum(item["size"] for item in verified_files)

    repeated = client.post("/api/v1/setup/existing-installations/adopt", headers=HEADERS, json={
        "project_id": project,
        "path": str(root),
        "snapshot": report["snapshot"],
        "instance_name": "shine-h3",
        "port": 8188,
    })
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["status"] == "ALREADY_ADOPTED"
    assert repeated.json()["installed_h3_bytes"] == sum(item["size"] for item in verified_files)
    assert repeated.json()["copied_bytes"] == repeated.json()["downloaded_bytes"] == 0

    with factory() as db:
        instance = db.get(ComfyInstance, response.json()["instance_id"])
        assert instance.payload["adopted"] is True
        assert instance.payload["root"] == str(root.resolve())
        assert instance.status == "STOPPED"
        assert not deployment.installed_instance_blockers(db, settings, instance, catalog)
        assert len(list(db.scalars(select(InstallationPlan)))) == 1
        assert len(list(db.scalars(select(InstallationAttempt)))) == 1
    assert (root / "main.py").read_text(encoding="utf-8") == "# test only\n"


def test_adoption_recovers_legacy_component_summary_without_rehash_or_download(prep, tmp_path, monkeypatch):
    client, factory, settings = prep
    project = create_project(client, "legacy Comfy verification recovery")
    catalog = deployment.load_default_catalog()
    root, report, verified_files = _install_fixture(tmp_path, catalog)
    _seed_verification(factory, catalog, report, verified_files, summary_only=True)
    monkeypatch.setattr(adoption, "inspect", lambda _path, _catalog: report)
    monkeypatch.setattr("shine.comfyops.existing_installation.inspect", lambda _path, _catalog: report)

    response = client.post("/api/v1/setup/existing-installations/adopt", headers=HEADERS, json={
        "project_id": project, "path": str(root), "snapshot": report["snapshot"],
    })

    assert response.status_code == 200, response.text
    assert response.json()["installed_h3_bytes"] == sum(item["size"] for item in verified_files)
    assert response.json()["copied_bytes"] == response.json()["downloaded_bytes"] == 0
    with factory() as db:
        instance = db.get(ComfyInstance, response.json()["instance_id"])
        assert instance.payload["adoption"]["verified_h3_files"] == verified_files
        assert not deployment.installed_instance_blockers(db, settings, instance, catalog)


def test_adoption_blocks_incomplete_h3_hash_evidence_without_creating_an_instance(prep, tmp_path, monkeypatch):
    client, factory, _ = prep
    project = create_project(client, "incomplete existing-Comfy adoption")
    catalog = deployment.load_default_catalog()
    root, report, verified_files = _install_fixture(tmp_path, catalog)
    _seed_verification(factory, catalog, report, verified_files[:-1])
    monkeypatch.setattr(adoption, "inspect", lambda _path, _catalog: report)
    monkeypatch.setattr("shine.comfyops.existing_installation.inspect", lambda _path, _catalog: report)

    response = client.post("/api/v1/setup/existing-installations/adopt", headers=HEADERS, json={
        "project_id": project, "path": str(root), "snapshot": report["snapshot"],
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EXISTING_H3_MODEL_FILES_INCOMPLETE"
    with factory() as db:
        assert list(db.scalars(select(ComfyInstance))) == []


def test_adopted_identity_ignores_catalog_membership_but_not_missing_h3_files(prep, tmp_path, monkeypatch):
    client, factory, settings = prep
    project = create_project(client, "adopted catalog membership")
    catalog = deployment.load_default_catalog()
    root, report, verified_files = _install_fixture(tmp_path, catalog)
    _seed_verification(factory, catalog, report, verified_files)
    monkeypatch.setattr(adoption, "inspect", lambda _path, _catalog: report)
    monkeypatch.setattr("shine.comfyops.existing_installation.inspect", lambda _path, _catalog: report)
    response = client.post("/api/v1/setup/existing-installations/adopt", headers=HEADERS, json={
        "project_id": project, "path": str(root), "snapshot": report["snapshot"],
    })
    assert response.status_code == 200, response.text

    with factory() as db:
        instance = db.get(ComfyInstance, response.json()["instance_id"])
        # A newer catalog that groups files differently still describes the same
        # user installation and must not block it.
        revised = {**report, "snapshot": "b" * 64, "catalog_version": "1.1.1"}
        monkeypatch.setattr("shine.comfyops.existing_installation.inspect", lambda _path, _catalog: revised)
        assert not deployment.installed_instance_blockers(db, settings, instance, catalog)
        h3_row = next(row for row in report["matches"] if row["component_id"].startswith("minimax-h3-"))
        missing = {**report, "matches": [row for row in report["matches"] if row is not h3_row]}
        monkeypatch.setattr("shine.comfyops.existing_installation.inspect", lambda _path, _catalog: missing)
        assert "ADOPTED_COMFY_FILES_CHANGED" in deployment.installed_instance_blockers(
            db, settings, instance, catalog)


def test_adopted_node_tree_change_invalidates_installation_evidence(prep, tmp_path, monkeypatch):
    client, factory, settings = prep
    project = create_project(client, "adopted node identity")
    catalog = deployment.load_default_catalog()
    root, report, verified_files = _install_fixture(tmp_path, catalog)
    _seed_verification(factory, catalog, report, verified_files)
    monkeypatch.setattr(adoption, "inspect", lambda _path, _catalog: report)
    monkeypatch.setattr("shine.comfyops.existing_installation.inspect", lambda _path, _catalog: report)
    response = client.post("/api/v1/setup/existing-installations/adopt", headers=HEADERS, json={
        "project_id": project, "path": str(root), "snapshot": report["snapshot"],
    })
    assert response.status_code == 200, response.text
    node = next((root / "custom_nodes").glob("ComfyUI-KJNodes"))
    (node / "__init__.py").write_text("# changed after confirmation\n", encoding="utf-8")

    with factory() as db:
        instance = db.get(ComfyInstance, response.json()["instance_id"])
        blockers = deployment.installed_instance_blockers(db, settings, instance, catalog)
        assert "ADOPTED_COMFY_NODES_CHANGED" in blockers

"""Machine-level resource discovery (2026-11-15 consolidation plan, WP3).

The reported gaps: only the first shallow-scan ComfyUI was inspected, weights
spread over other drives were invisible, and an empty database could not hold
an installation plan.  These tests lock the replacement facts:

* search directories are durable, deduplicated and local-only;
* a bounded batch scan finds renamed weights (size filter), node packs and
  API workflows across several roots, and reports its own coverage;
* a truncated seed continues in the next batch instead of claiming coverage;
* an unreadable area is named, never silently skipped;
* found-on-disk stays a candidate, separate from verified bytes;
* installation plans can belong to the machine (no project yet).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from shine.comfyops import deployment, resource_discovery
from shine.comfyops.models import InstallationPlan
from shine.models import AppSetting

LOCAL = {"x-shine-local-action": "1"}


@pytest.fixture
def env(studio):
    client, factory, tmp_path, settings = studio
    return client, factory, tmp_path, settings


def _first_model_spec() -> dict:
    catalog = deployment.load_default_catalog()
    for component in catalog["components"]:
        if component.get("kind") == "model" and component.get("files"):
            spec = component["files"][0]
            return {"component_id": component["component_id"], **spec}
    raise AssertionError("default catalog has no model file")


def _sparse_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.truncate(size)


def _fake_comfy(root: Path) -> None:
    (root / "comfy").mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("# ComfyUI entry\n", encoding="utf-8")


def _drain_scan(factory, limit: int = 40) -> None:
    for _ in range(limit):
        with factory() as db:
            result = resource_discovery.advance_scan(db, budget_s=5.0)
        if result["status"] not in {"QUEUED", "RUNNING"}:
            return
    raise AssertionError("scan did not finish within the batch limit")


def test_search_directories_are_durable_deduplicated_and_local_only(env):
    client, factory, tmp_path, _ = env
    chosen = tmp_path / "下载 目录"
    chosen.mkdir()
    first = client.post("/api/v1/setup/search-directories", json={"path": str(chosen)}, headers=LOCAL)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "ADDED"
    again = client.post("/api/v1/setup/search-directories", json={"path": str(chosen)}, headers=LOCAL)
    assert again.json()["status"] == "ALREADY_PRESENT"
    listing = client.get("/api/v1/setup/search-directories").json()["directories"]
    assert [row["path"] for row in listing] == [str(chosen.resolve())]
    removed = client.delete(f"/api/v1/setup/search-directories/{listing[0]['id']}", headers=LOCAL)
    assert removed.json()["status"] == "REMOVED"
    assert client.get("/api/v1/setup/search-directories").json()["directories"] == []
    network = client.post("/api/v1/setup/search-directories", json={"path": "//server/share"}, headers=LOCAL)
    assert network.status_code == 422


def test_scan_finds_renamed_weights_nodes_and_workflows_across_roots(env):
    client, factory, tmp_path, _ = env
    spec = _first_model_spec()

    # Root A: a ComfyUI checkout whose models tree holds the pinned byte size.
    root_a = tmp_path / "盘A" / "ComfyUI"
    _fake_comfy(root_a)
    _sparse_file(root_a / "models" / "diffusion_models" / "官方名.safetensors", spec["size"])

    # Root B: no ComfyUI at all — a renamed weight, a node pack, a workflow.
    root_b = tmp_path / "盘B" / "模型散放"
    _sparse_file(root_b / "改过的名字.bin", spec["size"])
    (root_b / "ComfyUI-KJNodes").mkdir(parents=True)
    (root_b / "我的 workflow.json").write_text(
        '{"31": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}}',
        encoding="utf-8")

    for path in (tmp_path / "盘A", root_b):
        assert client.post("/api/v1/setup/search-directories",
                           json={"path": str(path)}, headers=LOCAL).status_code == 200
    with factory() as db:
        resource_discovery.start_scan(db, drives=False)
    _drain_scan(factory)

    report = client.get("/api/v1/setup/resource-scan").json()
    assert report["status"] == "DONE"
    assert report["coverage_complete"] is True
    roots = [row["root"] for row in report["resources"]["comfy_roots"]]
    assert str(root_a.resolve()) in roots
    h3 = report["capabilities"]["h3"]
    found = next(row for row in h3["found"]
                 if row["component_id"] == spec["component_id"]
                 and row["relative_path"] == spec["relative_path"])
    # Both the official-named and the renamed file are candidates (size match).
    assert len(found["sources"]) == 2
    assert any("改过的名字" in source for source in found["sources"])
    assert any(row["component_id"] == "ComfyUI-KJNodes"
               for row in report["resources"]["node_dirs"])
    assert any("workflow" in row["path"] for row in report["resources"]["workflows"])
    # A candidate is not a verified byte.
    assert report["runtime_verified"] is False


def test_truncated_seed_continues_in_the_next_batch(env, monkeypatch):
    client, factory, tmp_path, _ = env
    monkeypatch.setattr(resource_discovery, "SCAN_BATCH_DIRECTORY_BUDGET", 3)
    deep = tmp_path / "深目录"
    for index in range(8):
        (deep / f"层{index}" / "子目录").mkdir(parents=True)
    client.post("/api/v1/setup/search-directories", json={"path": str(deep)}, headers=LOCAL)
    with factory() as db:
        resource_discovery.start_scan(db, drives=False)
    with factory() as db:
        first = resource_discovery.advance_scan(db, budget_s=5.0)
    assert first["coverage"]["status"] == "TRUNCATED"
    assert first["remaining"] >= 1  # the same seed keeps its place
    _drain_scan(factory)
    report = client.get("/api/v1/setup/resource-scan").json()
    assert report["coverage_complete"] is True


def test_unreadable_area_is_named_not_skipped(env, monkeypatch):
    client, factory, tmp_path, _ = env
    root = tmp_path / "含坏区"
    (root / "可读").mkdir(parents=True)
    blocked = root / "不可读"
    blocked.mkdir()

    real_scandir = os.scandir

    def guarded_scandir(path):
        if str(path) == str(blocked):
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(resource_discovery.os, "scandir", guarded_scandir)
    client.post("/api/v1/setup/search-directories", json={"path": str(root)}, headers=LOCAL)
    with factory() as db:
        resource_discovery.start_scan(db, drives=False)
    _drain_scan(factory)
    report = client.get("/api/v1/setup/resource-scan").json()
    coverage = report["coverage"][str(root.resolve())]
    assert str(blocked) in coverage["unreadable"]
    assert coverage["status"] == "DONE"


def test_report_keeps_found_and_missing_apart(env):
    client, factory, tmp_path, _ = env
    spec = _first_model_spec()
    root = tmp_path / "只有一半"
    _sparse_file(root / "one.bin", spec["size"])
    client.post("/api/v1/setup/search-directories", json={"path": str(root)}, headers=LOCAL)
    with factory() as db:
        resource_discovery.start_scan(db, drives=False)
    _drain_scan(factory)
    report = client.get("/api/v1/setup/resource-scan").json()
    h3 = report["capabilities"]["h3"]
    assert any(row["component_id"] == spec["component_id"] for row in h3["found"])
    # The rest of the pinned catalogue is still missing, by name.
    assert h3["missing"], "a partial disk must not read as a complete capability"
    assert all(row["sources"] == [] for row in h3["missing"])


def test_machine_level_installation_plan_needs_no_project(env):
    _, factory, _, _ = env
    with factory.begin() as db:
        plan = InstallationPlan(
            project_id=None,
            payload={"kind": "INSTALL", "catalog": "h3-ref2va-windows-x64",
                     "packages": [], "machine_level": True},
            sha256="m" * 64, status="AWAITING_APPROVAL",
        )
        db.add(plan)
        db.flush()
        plan_id = plan.id
    with factory() as db:
        row = db.get(InstallationPlan, plan_id)
        assert row.project_id is None
        assert row.payload["machine_level"] is True


def test_existing_installations_merges_the_durable_index(env):
    client, factory, tmp_path, _ = env
    root = tmp_path / "后来的安装" / "ComfyUI"
    _fake_comfy(root)
    with factory.begin() as db:
        state = resource_discovery.read_state(db)
        state["resources"]["comfy_roots"].append(
            {"root": str(root.resolve()), "found_via": "user"})
        row = db.get(AppSetting, resource_discovery.STATE_KEY)
        if row is None:
            db.add(AppSetting(key=resource_discovery.STATE_KEY, value=state))
        else:
            row.value = state
    listing = client.get("/api/v1/setup/existing-installations").json()
    assert str(root.resolve()) in listing["installations"]
    assert listing["deep_scan"]["indexed_comfy_roots"] == 1
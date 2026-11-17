"""检索 → 核验 → 复用 → 下载是同一条链。

复现过的四件事：添加目录后队列有任务但状态仍是 ``IDLE``；重新检索跳过上一轮
已扫完的目录，之后新增的文件永远发现不了；指定目录里与清单字节一致的改名权重
被找到后仍进入下载计划；已核验的文件被移走后页面仍显示 ``FILES_FOUND`` 和
``FILES_VERIFIED``。

这里固定：添加目录就交给现有扫描任务；重新检索能发现新增文件；整备先消费检索
候选和现有核验凭据，只有本机真的没有的字节才需要下载；旧登记不能替代当前文件
证据。
"""

from __future__ import annotations

import hashlib

import pytest

from shine.comfyops import deployment, resource_discovery, setup_summary
from shine.comfyops.setup_reconcile import reconcile_installation

pytestmark = pytest.mark.integration

PAYLOAD = b"\x89H3" + b"weight-bytes-" * 32
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _catalog() -> dict:
    return {
        "catalog_id": "test-catalog",
        "catalog_version": "1.0",
        "components": [{
            "component_id": "minimax-h3-test", "kind": "model", "revision": "r1",
            "license_id": "test-license", "usage": "research",
            "destination_family": "diffusion_models",
            "files": [{"relative_path": "w.safetensors", "sha256": SHA,
                       "size": len(PAYLOAD), "url": "https://example.invalid/w",
                       "mirrors": []}],
        }],
    }


def _stub_scan_catalog(monkeypatch) -> None:
    """The scan matches by pinned size; register this tiny file as a catalog item."""
    monkeypatch.setattr(resource_discovery, "_model_specs", lambda: {
        len(PAYLOAD): [{"catalog_id": "test-catalog", "component_id": "minimax-h3-test",
                        "relative_path": "w.safetensors", "sha256": SHA, "size": len(PAYLOAD)}],
    })


def _installation(root) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("# test installation\n", encoding="utf-8")
    (root / "comfy").mkdir(exist_ok=True)
    (root / "models" / "diffusion_models").mkdir(parents=True, exist_ok=True)
    return {"root": str(root), "instance_id": None, "port": None}


def _report(factory, settings, installation) -> dict:
    with factory() as db:
        return setup_summary.capability_report(
            db, settings, "h3", installation=installation, project_id=None)


def test_adding_a_directory_queues_the_existing_scan_task(tmp_path, api):
    client, factory, _settings = api
    folder = tmp_path / "weights"
    folder.mkdir()
    with factory.begin() as db:
        added = resource_discovery.add_directory(db, str(folder))
    assert added["status"] == "ADDED"
    assert added["scan"]["status"] == "QUEUED", "队列有任务，状态不能还停在 IDLE"
    with factory() as db:
        state = resource_discovery.report(db)
    assert state["status"] == "QUEUED"
    assert state["queue_remaining"] >= 1


def test_a_rescan_finds_a_file_that_arrived_after_the_first_round(tmp_path, api, monkeypatch):
    client, factory, _settings = api
    _stub_scan_catalog(monkeypatch)
    folder = tmp_path / "downloads"
    folder.mkdir()
    with factory() as db:
        resource_discovery.add_directory(db, str(folder))
    with factory() as db:
        resource_discovery.advance_scan(db, budget_s=5.0)
    with factory() as db:
        first = resource_discovery.report(db)
    assert first["status"] == "DONE"

    (folder / "arrived-later.safetensors").write_bytes(PAYLOAD)
    with factory() as db:
        queued = resource_discovery.start_scan(db, drives=False)
    assert queued["queued"] >= 1, "重新检索不能跳过上一轮扫完的目录"
    with factory() as db:
        resource_discovery.advance_scan(db, budget_s=5.0)
    with factory() as db:
        candidates = resource_discovery.verification_candidates(db, _catalog())
    assert [row["path"] for row in candidates] == [str(folder / "arrived-later.safetensors")]


def test_a_verified_search_candidate_is_reused_instead_of_downloaded(tmp_path, api, monkeypatch):
    client, factory, settings = api
    _stub_scan_catalog(monkeypatch)
    catalog = _catalog()
    monkeypatch.setattr(setup_summary, "_catalog_for", lambda capability: catalog)
    from shine.comfyops import setup_reconcile

    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    installation = _installation(tmp_path / "ComfyUI")
    search = tmp_path / "renamed-weights"
    search.mkdir()
    # 与清单字节一致，但文件名完全不同：只有逐字节核验才能认出来。
    (search / "some-other-name.bin").write_bytes(PAYLOAD)
    with factory() as db:
        resource_discovery.add_directory(db, str(search))
        resource_discovery.advance_scan(db, budget_s=5.0)

    with factory() as db:
        result = reconcile_installation(db, settings, path=installation["root"], capability="h3")
    assert result["scan_candidates"] == 1
    assert result["cached_from_scan"] == [SHA], "找到的字节要进现有缓存，才不会被再下载一次"
    cache = settings.comfyops_dir / "isolated-packages" / ".cache" / SHA
    assert cache.is_file()

    report = _report(factory, settings, installation)
    assert report["facts"]["FILES_VERIFIED"] is True
    assert report["cached_files"] == 1
    assert report["missing_bytes"] == 0, "字节已在本机，下载字节必须是 0"

    with factory() as db:
        plan = deployment.create_install_plan(db, settings, catalog, ["minimax-h3-test"], None, None)
    assert plan.payload["download_bytes"] == 0
    assert plan.payload["cached_bytes"] == len(PAYLOAD)


def test_a_moved_verified_file_stops_counting_as_found(tmp_path, api, monkeypatch):
    client, factory, settings = api
    catalog = _catalog()
    monkeypatch.setattr(setup_summary, "_catalog_for", lambda capability: catalog)
    from shine.comfyops import setup_reconcile

    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    installation = _installation(tmp_path / "ComfyUI")
    weight = tmp_path / "ComfyUI" / "models" / "diffusion_models" / "w.safetensors"
    weight.write_bytes(PAYLOAD)

    with factory() as db:
        result = reconcile_installation(db, settings, path=installation["root"], capability="h3")
    assert result["complete"] is True
    before = _report(factory, settings, installation)
    assert before["facts"]["FILES_FOUND"] is True
    assert before["facts"]["FILES_VERIFIED"] is True

    # 人把已核验的文件移走了：旧登记仍在，但当前没有那份字节。
    weight.unlink()
    after = _report(factory, settings, installation)
    assert after["facts"]["FILES_FOUND"] is False, "旧登记不能替代当前文件证据"
    assert after["facts"]["FILES_VERIFIED"] is False
    assert [row["relative_path"] for row in after["missing_files"]] == ["w.safetensors"]

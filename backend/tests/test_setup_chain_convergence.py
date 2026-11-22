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
from pathlib import Path

import pytest

from shine.comfy.workflow import graph_hash
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


def test_a_verified_search_candidate_is_reused_instead_of_downloaded(tmp_path, api, monkeypatch, fake_deployment_host):
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
    # 已核验的缓存字节不是「缺件」：它只欠一次本地落位（执行者的计划），
    # 卡片不得在「文件已就绪并核验」旁边再报缺少文件。
    assert report["missing_files"] == []
    assert [row["relative_path"] for row in report["unplaced_files"]] == ["w.safetensors"]
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


def test_manual_staging_is_consumed_by_the_same_reconcile_executor(tmp_path, api, monkeypatch, fake_deployment_host):
    """手动收料目录必须被整备链的核验执行者消费，而不是只有独立 API 认识它。

    真机复现（2026-11-17）：用户把完整权重放进 manual-downloads 后点一键整备，
    RECONCILE 仍报缺件并重排下载——收料函数没挂进 reconcile_installation。
    """
    client, factory, settings = api
    catalog = _catalog()
    monkeypatch.setattr(setup_summary, "_catalog_for", lambda capability: catalog)
    from shine.comfyops import setup_reconcile

    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    installation = _installation(tmp_path / "ComfyUI")
    staging = Path(settings.comfyops_dir) / "manual-downloads"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "w.safetensors").write_bytes(PAYLOAD)

    with factory() as db:
        first = setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="h3")
    assert first["complete"] is True, first
    assert first["manual_imported_bytes"] == len(PAYLOAD)
    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache" / SHA
    assert cache.is_file(), "收料必须进入既有内容地址缓存"
    assert (staging / "w.safetensors").is_file(), "原件留在原处，不被搬走"

    # 重复核验：缓存已是证据，complete 不得翻回 false。
    with factory() as db:
        again = setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="h3")
    assert again["complete"] is True, again
    report = _report(factory, settings, installation)
    assert report["facts"]["FILES_VERIFIED"] is True
    # 缓存字节仍需放置（本地复制），但不再欠任何下载字节。
    with factory() as db:
        plan = deployment.create_install_plan(db, settings, catalog, ["minimax-h3-test"], None, None)
    assert plan.payload["download_bytes"] == 0
    assert plan.payload["cached_bytes"] == len(PAYLOAD)


def test_relayvsr_places_verified_bytes_into_the_served_models_root(tmp_path, api, monkeypatch):
    """用户裁定 2026-11-17：RelayVSR 权重加性落位到执行它的 ComfyUI models 目录。

    复现：落位写 isolated-packages 而 readiness/精修读收编 ComfyUI 的 models，
    卡片②永远「跑不通」、按钮空转。落位必须与读取同根，且绝不覆盖已有文件。
    """
    client, factory, settings = api
    catalog = _catalog()
    from shine.comfyops import setup_reconcile

    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    installation = _installation(tmp_path / "ComfyUI")
    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / SHA).write_bytes(PAYLOAD)

    with factory() as db:
        first = setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="relayvsr")
    target = Path(installation["root"]) / "models" / "w.safetensors"
    assert target.is_file() and target.read_bytes() == PAYLOAD
    assert first["served_root_placed"] == ["w.safetensors"]

    mtime = target.stat().st_mtime_ns
    with factory() as db:
        setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="relayvsr")
    assert target.stat().st_mtime_ns == mtime, "重复核验不得重写已落位文件"

    target.write_bytes(b"evil")
    with factory() as db:
        third = setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="relayvsr")
    assert target.read_bytes() == b"evil", "同名不同字节拒绝覆盖，保留用户文件"
    assert third["served_root_placed"] == []


def test_manual_reconcile_reuses_the_cached_digest_without_a_full_reread(tmp_path, api, monkeypatch):
    """R5 B3/INV-02/05：已缓存字节判同走回执复用，不得每轮全量重读几十 GiB。

    复现方向：manual_files_reconcile 对每个已缓存文件裸哈希判同，整备每轮
    RECONCILE 都全量重读缓存。威胁模型不变：stat+头尾见证仍区分同 tick 改写。
    """
    client, factory, settings = api
    catalog = _catalog()
    from shine.comfyops import setup_reconcile, verification_receipts

    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    staging = Path(settings.comfyops_dir) / "manual-downloads"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "w.safetensors").write_bytes(PAYLOAD)
    with factory() as db:
        first = setup_reconcile.manual_files_reconcile(db, settings, capability="h3", catalog=catalog)
    assert first["imported_bytes"] == len(PAYLOAD)
    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"

    seen: list[tuple[str, bool]] = []
    real_digest = verification_receipts.file_digest

    def spy(path, expected_sha256, expected_size, cache_dir=None):
        result = real_digest(path, expected_sha256, expected_size, cache_dir=cache_dir)
        seen.append((str(path), bool(result.get("reused"))))
        return result

    monkeypatch.setattr(verification_receipts, "file_digest", spy)
    original_hash = setup_reconcile._hash_file

    def guarded(path):
        if Path(path).parent == cache:
            raise AssertionError("FULL_REHASH_OF_CACHED_BYTES")
        return original_hash(path)

    monkeypatch.setattr(setup_reconcile, "_hash_file", guarded)
    # 第二轮建立缓存回执（首次允许读一遍）；第三轮必须复用，不再全量读。
    with factory() as db:
        setup_reconcile.manual_files_reconcile(db, settings, capability="h3", catalog=catalog)
    seen.clear()
    with factory() as db:
        third = setup_reconcile.manual_files_reconcile(db, settings, capability="h3", catalog=catalog)
    assert third["imported"] == []
    assert (str(cache / SHA), True) in seen, "已缓存字节必须命中回执复用"


def test_relayvsr_served_check_reuses_the_receipt_instead_of_rehashing(tmp_path, api, monkeypatch):
    """R5 B3：已落位文件的判同也走同一回执执行者，不每轮全量重读。"""
    client, factory, settings = api
    catalog = _catalog()
    from shine.comfyops import setup_reconcile, verification_receipts

    monkeypatch.setattr(setup_reconcile, "_capability_catalog", lambda capability: catalog)
    installation = _installation(tmp_path / "ComfyUI")
    cache = Path(settings.comfyops_dir) / "isolated-packages" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / SHA).write_bytes(PAYLOAD)
    with factory() as db:
        setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="relayvsr")
    target = Path(installation["root"]) / "models" / "w.safetensors"
    assert target.is_file()

    seen: list[tuple[str, bool]] = []
    real_digest = verification_receipts.file_digest

    def spy(path, expected_sha256, expected_size, cache_dir=None):
        result = real_digest(path, expected_sha256, expected_size, cache_dir=cache_dir)
        seen.append((str(path), bool(result.get("reused"))))
        return result

    monkeypatch.setattr(verification_receipts, "file_digest", spy)
    original_hash = setup_reconcile._hash_file

    def guarded(path):
        if Path(path) == target:
            raise AssertionError("FULL_REHASH_OF_SERVED_BYTES")
        return original_hash(path)

    monkeypatch.setattr(setup_reconcile, "_hash_file", guarded)
    with factory() as db:
        setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="relayvsr")
    seen.clear()
    with factory() as db:
        setup_reconcile.reconcile_installation(
            db, settings, path=installation["root"], capability="relayvsr")
    assert (str(target), True) in seen, "已落位文件判同必须命中回执复用"
    assert target.read_bytes() == PAYLOAD


def test_presence_accepts_family_prefixed_relative_paths(tmp_path, api):
    """RelayVSR 清单的 relative_path 带 family 前缀；presence 不得叠双层目录。

    复现：权重已正确落位 models/diffusion_models/…，卡片仍报「缺 2 个文件」，
    因为 presence 拼成 models/diffusion_models/diffusion_models/…。
    """
    client, factory, settings = api
    catalog = _catalog()
    catalog["components"][0]["files"][0]["relative_path"] = "diffusion_models/w.safetensors"
    models = tmp_path / "models"
    (models / "diffusion_models").mkdir(parents=True)
    (models / "diffusion_models" / "w.safetensors").write_bytes(PAYLOAD)
    presence = setup_summary.model_file_presence(models, catalog)
    assert presence["missing"] == []
    assert presence["present"], presence


def _stopped_install_records(factory, *, attempt_plan_hash="correct", with_spec=True):
    """一条停摆的本地隔离安装记录：FAILED_RECONCILE_REQUIRED、无租约、stage 待收尾。"""
    from shine.comfyops.models import InstallationAttempt, InstallationPlan
    from shine.models import utcnow

    spec = {
        "name": "stopped-pkg", "kind": "model", "revision": "r1",
        "license_id": "test-license", "usage": "research",
        "files": [{"relative_path": "w.bin", "sha256": "a" * 64, "size": 4,
                   "url": "https://example.invalid/w"}],
    }
    package = {"id": "stopped-pkg", "hash": graph_hash(spec)}
    if with_spec:
        package["spec"] = spec
    payload = {"executor": "LOCAL_ISOLATED", "isolated": True, "packages": [package]}
    plan_sha = graph_hash(payload)
    with factory.begin() as db:
        plan = InstallationPlan(project_id=None, payload=payload, sha256=plan_sha, status="NEEDS_REVIEW")
        db.add(plan)
        db.flush()
        attempt_payload = {"plan_hash": plan_sha if attempt_plan_hash == "correct" else "0" * 64}
        attempt = InstallationAttempt(
            plan_id=plan.id, payload=attempt_payload, sha256=graph_hash(attempt_payload),
            stage="NEEDS_REVIEW", status="FAILED_RECONCILE_REQUIRED",
            error_code="DOWNLOAD_FAILED", lease_owner=None, heartbeat_at=utcnow(),
        )
        db.add(attempt)
        db.flush()
        return plan.id, attempt.id


def test_a_stopped_attempt_with_a_wrong_plan_digest_stays_blocked(tmp_path, api):
    """计划摘要对不上的停摆记录是对账硬校验：不能被收编成 RECONCILED_NO_RETRY。"""
    client, factory, settings = api
    _plan_id, attempt_id = _stopped_install_records(factory, attempt_plan_hash="wrong")
    from shine.comfyops.local import reconcile_stopped_isolated_attempts

    with factory.begin() as db:
        reconciled = reconcile_stopped_isolated_attempts(db, settings, actor="worker:setup")
    assert attempt_id not in [row["attempt_id"] for row in reconciled]
    with factory() as db:
        from shine.comfyops.models import InstallationAttempt as Attempt

        assert db.get(Attempt, attempt_id).status == "FAILED_RECONCILE_REQUIRED"


def test_a_stopped_attempt_with_a_package_entry_missing_its_spec_stays_blocked(tmp_path, api):
    """包条目缺 spec 是损坏的记录，不是可跳过的条目：静默收编会留下误晋升。"""
    client, factory, settings = api
    _plan_id, attempt_id = _stopped_install_records(factory, with_spec=False)
    from shine.comfyops.local import reconcile_stopped_isolated_attempts

    with factory.begin() as db:
        reconciled = reconcile_stopped_isolated_attempts(db, settings, actor="worker:setup")
    assert attempt_id not in [row["attempt_id"] for row in reconciled]
    with factory() as db:
        from shine.comfyops.models import InstallationAttempt as Attempt

        assert db.get(Attempt, attempt_id).status == "FAILED_RECONCILE_REQUIRED"


def test_a_reconciled_stopped_attempt_writes_one_consistent_terminal_state(tmp_path, api):
    """合法停摆记录收尾时，status 与 stage 必须一次写成同一个终态。"""
    client, factory, settings = api
    plan_id, attempt_id = _stopped_install_records(factory)
    from shine.comfyops.local import reconcile_stopped_isolated_attempts

    with factory.begin() as db:
        reconciled = reconcile_stopped_isolated_attempts(db, settings, actor="worker:setup")
    assert attempt_id in [row["attempt_id"] for row in reconciled]
    with factory() as db:
        from shine.comfyops.models import InstallationAttempt as Attempt
        from shine.comfyops.models import InstallationPlan as Plan

        attempt = db.get(Attempt, attempt_id)
        assert attempt.status == "RECONCILED_NO_RETRY"
        assert attempt.stage not in {"NEEDS_REVIEW", "INSTALLING", None}, "stage 不得停留在待核对"
        assert db.get(Plan, plan_id).status == "RECONCILED_NO_RETRY"

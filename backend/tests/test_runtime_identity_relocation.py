"""环境整备状态跟着「这台机器」走，不跟着「这一次的目录」走。

复现：上一轮 worktree `shine-video-studio-director-dev` 里激活的运行身份把
`managed_recipe.json`、两个审片 manifest 以及两个仓库脚本钉在那个 worktree 的
绝对路径下；worktree 删掉后，从主仓启动 → `RUNTIME_PINNED_FILE_MISSING` →
`ACTIVE_RUNTIME_CONFIGURATION_CHANGED` → 徽标「真实制作暂不可用 · 固定文件发生了
变化」，每次启动都被要求重走基础配置。

用户的判定很直接：检索与激活都已经做过，换一次启动目录不该再配一遍。这里固定
一个裁决（`same_configuration`）：文件**字节**、实例、清单、配方与渲染端点全都
没变，只是账本目录搬了家，就仍是同一套配置；任何真实改动（改了渲染器文件、换了
实例、改了配方）照旧阻断。
"""

# ruff: noqa: F811
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shine.comfy.workflow import graph_hash
from shine.comfyops import resource_discovery
from shine.comfyops.deployment import REPO_ROOT
from shine.comfyops.managed_recipe import build_renderer_launch, recipe_path
from shine.comfyops.runtime_configuration import (
    ACTIVE_KEY,
    SERVICE_EVIDENCE_KEY,
    _evidence_seal,
    configuration_fingerprint,
    configuration_identity,
    load_runtime_recipe,
    record_service_evidence,
    resolve_runtime,
    same_configuration,
    service_evidence_status,
)
from shine.config import Settings
from shine.models import AppSetting

pytestmark = pytest.mark.integration

# `candidate` / `prep` / `activate` are the established managed-installation
# fixtures: a real recipe, a registered instance and a pinned file set.
from tests.test_runtime_activation import activate, candidate, prep  # noqa: F401, E402


def _removed_worktree(settings) -> Path:
    return settings.data_dir.parent / "removed-worktree"


def _records_after_relocation(db, settings) -> dict:
    """Rewrite the recorded identity as a previous worktree left it."""
    active = db.get(AppSetting, ACTIVE_KEY)
    legacy = json.loads(json.dumps(active.value["identity"]))
    legacy["recipe"] = str(_removed_worktree(settings) / "data" / "comfyops"
                           / "setup" / "managed_recipe.json")
    legacy["files"] = {
        path.replace(str(settings.comfyops_dir), str(_removed_worktree(settings)
                                                     / "data" / "comfyops")): digest
        for path, digest in (active.value["identity"]["files"] or {}).items()
    }
    active.value = {**active.value, "identity": legacy, "sha256": graph_hash(legacy)}
    evidence = {
        "version": 1,
        "configuration_sha256": graph_hash(legacy),
        "recipe_sha256": legacy["recipe_sha256"],
        "instance_id": legacy["instance_id"],
        "instance_sha256": legacy["instance_sha256"],
    }
    evidence["sha256"] = _evidence_seal(evidence)
    row = db.get(AppSetting, SERVICE_EVIDENCE_KEY)
    if row is None:
        db.add(AppSetting(key=SERVICE_EVIDENCE_KEY, value=evidence))
    else:
        row.value = evidence
    db.flush()  # the session does not autoflush; record_service_evidence reads it back
    return legacy


def _recipe_pointing_at_the_removed_worktree(settings) -> Path:
    """The recipe file itself still names the deleted worktree, byte for byte.

    This is what an actual copy leaves behind: the JSON is unchanged, so its
    digest still matches the activated recipe, while every bookkeeping path
    inside it points at a directory that no longer exists.
    """
    recipe_file = recipe_path(settings)
    gone = str(_removed_worktree(settings))
    payload = json.loads(recipe_file.read_text(encoding="utf-8"))
    renderer = payload["renderer"]

    def moved(value: str) -> str:
        return str(value).replace(str(settings.comfyops_dir), gone + "/data/comfyops").replace(
            str(REPO_ROOT), gone)

    for key in ("runtime_data_root", "bootstrap", "log_file"):
        if renderer.get(key):
            renderer[key] = moved(renderer[key])
    renderer["argv"] = [moved(item) for item in renderer["argv"]]
    renderer["pinned_files"] = [moved(item) for item in renderer["pinned_files"]]
    for spec in (payload.get("reviews") or {}).values():
        for key in ("manifest", "runtime_packages"):
            spec[key] = moved(spec[key])
        spec["argv"] = [moved(item) for item in spec["argv"]]
    recipe_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not _removed_worktree(settings).exists()
    return recipe_file



def test_a_moved_data_directory_keeps_real_production_ready(prep, candidate):
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    with factory.begin() as db:
        legacy = _records_after_relocation(db, settings)

    runtime = client.get("/api/v1/setup/runtime").json()
    assert runtime["status"] == "ACTIVE", runtime
    assert runtime["error"] is None, runtime
    assert runtime["services_verified"] is True, runtime

    status = client.get("/api/v1/system/status").json()
    assert status["production_mode"]["code"] != "ACTIVE_RUNTIME_CONFIGURATION_CHANGED"
    assert status["render_mode"] == "comfyui"

    recipe = settings.comfyops_dir / "setup" / "managed_recipe.json"
    with factory() as db:
        effective = resolve_runtime(db, settings)
        assert effective.render_provider == "comfyui"
        assert effective.managed_recipe_path == str(recipe)
        # 账本保持原样：不重写已封印的证据，只是读的时候按当前目录解析。
        assert db.get(AppSetting, ACTIVE_KEY).value["identity"]["recipe"] == legacy["recipe"]


def test_relocation_is_stable_across_repeated_launches(prep, candidate):
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    with factory.begin() as db:
        legacy = _records_after_relocation(db, settings)
    for _ in range(3):
        assert client.get("/api/v1/setup/runtime").json()["status"] == "ACTIVE"
    with factory() as db:
        assert db.get(AppSetting, ACTIVE_KEY).value["identity"]["recipe"] == legacy["recipe"]
        assert resolve_runtime(db, settings).render_provider == "comfyui"


def test_a_really_changed_renderer_file_still_blocks(prep, candidate):
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    with factory.begin() as db:
        _records_after_relocation(db, settings)
    assert client.get("/api/v1/setup/runtime").json()["status"] == "ACTIVE"

    root = candidate[1]
    (root / "main.py").write_text("# contract fixture, but rewritten\n", encoding="utf-8")

    runtime = client.get("/api/v1/setup/runtime").json()
    assert runtime["status"] == "INVALID_REQUIRES_REVIEW"
    assert runtime["error"] == "ACTIVE_RUNTIME_CONFIGURATION_CHANGED"
    status = client.get("/api/v1/system/status").json()
    assert status["production_mode"]["code"] == "ACTIVE_RUNTIME_CONFIGURATION_CHANGED"


def test_an_edited_recipe_still_blocks(prep, candidate):
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    recipe = settings.comfyops_dir / "setup" / "managed_recipe.json"
    payload = json.loads(recipe.read_text(encoding="utf-8"))
    payload["renderer"]["port"] = int(payload["renderer"]["port"])
    payload["edited_by"] = "operator"
    recipe.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    runtime = client.get("/api/v1/setup/runtime").json()
    assert runtime["status"] == "INVALID_REQUIRES_REVIEW"
    assert runtime["error"] == "ACTIVE_RUNTIME_CONFIGURATION_CHANGED"


def test_a_recipe_naming_a_deleted_worktree_still_launches_from_here(prep, candidate):
    """配方字节没变，但里面的路径全指向已删除的 worktree：仍按现位置起机。"""
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    recipe_file = _recipe_pointing_at_the_removed_worktree(settings)

    with factory.begin() as db:
        identity = configuration_identity(db, settings, recipe_file)
        active = db.get(AppSetting, ACTIVE_KEY)
        active.value = {**active.value, "identity": identity, "sha256": graph_hash(identity)}

    runtime = client.get("/api/v1/setup/runtime").json()
    assert runtime["status"] == "ACTIVE", runtime
    assert runtime["error"] is None, runtime

    with factory() as db:
        effective = resolve_runtime(db, settings)
        recipe, _sha = load_runtime_recipe(settings)
    assert effective.render_provider == "comfyui"
    assert effective.managed_recipe_path == str(recipe_file)
    renderer = recipe["renderer"]
    assert Path(renderer["runtime_data_root"]).is_relative_to(settings.comfyops_dir.resolve())
    assert Path(renderer["bootstrap"]) == REPO_ROOT / "scripts" / "managed_comfy_bootstrap.py"
    argv, _root, _env, pins, _token, _port = build_renderer_launch(renderer)
    gone = str(_removed_worktree(settings))
    assert not [item for item in argv if item.startswith(gone)], argv
    assert all(Path(pin).is_file() for pin in pins), pins


def test_an_upgraded_bootstrap_is_not_an_environment_change(tmp_path):
    """仓库自带的启动器随版本走；操作员装的环境变了才阻断。"""
    settings = Settings(data_dir=tmp_path, database_url=f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    recipe_file = recipe_path(settings)
    recipe_file.parent.mkdir(parents=True, exist_ok=True)
    recipe_file.write_text(json.dumps({
        "renderer": {"bootstrap": r"F:\old\scripts\managed_comfy_bootstrap.py"},
        "reviews": {"VLM": {"manifest": r"F:\old\comfyops\setup\manifests\vlm.json",
                            "argv": ["{python}", r"F:\old\scripts\local_review_isolated_server.py"]}},
    }), encoding="utf-8")

    facts = {"instance_id": "22ea7c4e-316c-5ff3-a4aa-632186c81a5e", "instance_sha256": "a" * 64,
             "catalog_sha256": "b" * 64, "recipe_sha256": "c" * 64,
             "comfyui_base_url": "http://127.0.0.1:8188"}
    installed = {r"F:\machine\ComfyUI\main.py": "1" * 64, r"F:\machine\python.exe": "2" * 64,
                 r"F:\old\comfyops\setup\manifests\vlm.json": "5" * 64}
    stored = {**facts, "recipe": r"F:\old\comfyops\setup\managed_recipe.json",
              "files": {**installed,
                        r"F:\old\scripts\managed_comfy_bootstrap.py": "3" * 64,
                        r"F:\old\scripts\local_review_isolated_server.py": "7" * 64}}
    current = {**facts, "recipe": str(recipe_file),
               "files": {**installed,
                         str(REPO_ROOT / "scripts" / "managed_comfy_bootstrap.py"): "4" * 64,
                         str(REPO_ROOT / "scripts" / "local_review_isolated_server.py"): "8" * 64}}
    # 升级了启动器与审片入口 → 仍是同一台机器上的同一套配置。
    assert same_configuration(stored, current, settings=settings) is True

    rewritten = {**current, "files": {**current["files"], r"F:\machine\ComfyUI\main.py": "9" * 64}}
    assert same_configuration(stored, rewritten, settings=settings) is False
    # 审片 manifest 是安装事实（模型清单），不是仓库代码：改了要重验。
    other_manifest = {**current, "files": {**current["files"],
                                           r"F:\old\comfyops\setup\manifests\vlm.json": "6" * 64}}
    assert same_configuration(stored, other_manifest, settings=settings) is False
    other_instance = {**current, "instance_sha256": "d" * 64}
    assert same_configuration(stored, other_instance, settings=settings) is False


def test_a_resealed_validation_is_not_invalidated_by_the_directory(prep, candidate):
    """在新位置重新封印一次服务证据，之后搬家仍判为同一套配置。"""
    client, factory, settings = prep
    assert activate(prep, candidate).status_code == 200
    with factory.begin() as db:
        _records_after_relocation(db, settings)  # 激活记录仍钉在已删除目录
        current = configuration_identity(db, settings, recipe_path(settings))
        assert configuration_fingerprint(current) == configuration_fingerprint(
            db.get(AppSetting, ACTIVE_KEY).value["identity"])
        record_service_evidence(db, current, {"version": 1,
                                              "validated_at": "2026-11-17T09:00:00+08:00"})
    with factory() as db:
        status = service_evidence_status(db, settings)
    assert status["status"] == "READY", status
    assert status["blockers"] == []


def test_the_scan_index_survives_a_new_session(api, tmp_path):
    """检索结果落在台账里：换一个会话（等价于换一个进程）仍然读得到。"""
    client, factory, _settings = api
    folder = tmp_path / "weights"
    folder.mkdir()
    with factory() as db:
        resource_discovery.add_directory(db, str(folder))
    with factory() as db:
        resource_discovery.advance_scan(db, budget_s=5.0)

    with factory() as fresh_session:  # a new process opens the same data dir
        report = resource_discovery.report(fresh_session)
    assert [row["path"] for row in report["directories"]] == [str(folder)]
    assert report["status"] == "DONE"
    assert str(folder) in report["coverage"]

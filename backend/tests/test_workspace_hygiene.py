"""The workspace rules that keep the repository machine-independent.

``scripts/clean_test_workspace.py`` is the only executor of the cleanup rule and
``scripts/shine_test_root.py`` the only way a script learns the test root.  These
tests pin the rule itself and guard the repository against hardcoded locations.
"""
from __future__ import annotations

import importlib.util
import json
import os
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
THRESHOLD = 64 * 1024
BIG = 200 * 1024
SMALL = 1024
# Locations this repository must never point at again: a retired per-round directory
# inside the checkout, and directories that exist on one machine only.
RETIRED_LOCATIONS = (".local-acceptance", "f:\\shinetest", "f:\\claudeworkspace", "f:\\aigc2026")


def _load(name: str):
    source = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"shine_{name}_hygiene_test", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their module through sys.modules while the class body runs
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cleaner_module = _load("clean_test_workspace")
root_module = _load("shine_test_root")


def _touch(path: Path, size: int = SMALL) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


def _fake_weight(path: Path, *, zero_body: bool = False) -> None:
    """A valid safetensors header; the body is non-zero unless the file is a test fake."""
    header = json.dumps({"w": {"dtype": "F32", "shape": [1024], "data_offsets": [0, 4096]}}).encode()
    header += b" " * (-(8 + len(header)) % 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        stream.write(struct.pack("<Q", len(header)))
        stream.write(header)
        stream.write(b"\0" * 4096 if zero_body else b"\x01" * 4096)


def _make_dir_link(target: Path, link: Path) -> bool:
    """A directory junction on Windows, a symlink elsewhere; False when neither is possible."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except (OSError, AttributeError, ImportError):
        return False
    return os.path.lexists(link)


def _cleaner(root: Path, **options):
    return cleaner_module.WorkspaceCleaner(
        root, min_bytes=THRESHOLD, real_weight_min_bytes=SMALL, **options)


def _reports(root: Path, **options) -> dict[str, cleaner_module.EntryReport]:
    return {report.name: report for report in _cleaner(root, **options).apply()}


def _build_round(root: Path) -> Path:
    """One acceptance round laid out the way ``AGENTS.md`` §6.1 prescribes."""
    _touch(root / "chunk-0.log")
    _touch(root / "keep-me" / "a.bin", BIG)
    _touch(root / "bt-run" / "test_a0" / "x.bin")
    _touch(root / "bt-run" / "test_b0" / "y.bin")
    _touch(root / "bt-run" / "test_c0" / ".lock")
    shards = root / "shards-20260101-000000-000"
    _touch(shards / "bt-0" / "test_x0" / "w.bin", BIG)
    _touch(shards / "chunk-0.log")
    round_dir = root / "round"
    _touch(round_dir / "data" / "shine.db", BIG)
    _touch(round_dir / "data" / "run" / "queue.bin", BIG)
    _touch(round_dir / "comfyops" / "adopted-runtime" / "cache.bin", BIG)
    _touch(round_dir / "comfyops" / "isolated-packages" / ".cache" / "blob", BIG)
    _touch(round_dir / "comfyops" / "models" / "loras" / "x.bin", BIG)
    _touch(round_dir / "comfyops" / "setup" / "recipe.json", BIG)
    _touch(round_dir / "comfyops" / "logs" / "renderer.log", BIG)
    _touch(round_dir / "comfyops" / "handoff-history" / "a.json", BIG)
    _touch(round_dir / "evidence" / "shot.png", BIG)
    _fake_weight(round_dir / "weights" / "real.safetensors")
    _touch(round_dir / "weights" / "extra.bin", BIG)
    _touch(round_dir / "weights" / "config.json")
    _touch(round_dir / "fake-weights" / "fake.safetensors", BIG)
    _touch(root / "light" / "notes.json")
    (root / "empty" / "a" / "b").mkdir(parents=True)
    return round_dir


def test_dry_run_decides_without_deleting(tmp_path: Path) -> None:
    root = tmp_path / "test-root"
    round_dir = _build_round(root)

    reports = {report.name: report for report in _cleaner(root).plan()}

    assert reports["bt-run"].actions["removed-basetemp"] == 1
    # data, comfyops and fake-weights are the three data directories of the round
    assert reports["round"].actions["purged-data-dir"] == 3
    assert reports["round"].deleted_bytes == 4 * BIG
    assert (root / "bt-run" / "test_a0" / "x.bin").exists()
    assert (round_dir / "data" / "shine.db").exists()
    assert (round_dir / "comfyops" / "adopted-runtime" / "cache.bin").exists()
    assert (root / "empty" / "a" / "b").is_dir()


def test_apply_follows_the_single_cleanup_rule(tmp_path: Path) -> None:
    root = tmp_path / "test-root"
    round_dir = _build_round(root)
    shards = root / "shards-20260101-000000-000"

    reports = _reports(root, keep=["keep-me"])

    # rules 1 and 5: evidence stays
    assert (root / "chunk-0.log").exists()
    assert (root / "light" / "notes.json").exists()
    assert (root / "keep-me" / "a.bin").exists()
    assert reports["keep-me"].actions["kept-by-name"] == 1
    # rule 2: a basetemp goes wholesale, however small
    assert not (root / "bt-run").exists()
    # rule 3: a shard archive keeps its logs
    assert not (shards / "bt-0").exists()
    assert (shards / "chunk-0.log").exists()
    # rule 4: an empty tree goes
    assert not (root / "empty").exists()
    # rule 6: data goes, protected directories stay whole
    assert not (round_dir / "data").exists()
    assert not (round_dir / "comfyops" / "adopted-runtime").exists()
    assert not (round_dir / "fake-weights").exists()
    for kept in ("isolated-packages/.cache/blob", "models/loras/x.bin", "setup/recipe.json",
                 "logs/renderer.log", "handoff-history/a.json"):
        assert (round_dir / "comfyops" / kept).exists(), kept
    assert (round_dir / "evidence" / "shot.png").exists()
    assert (round_dir / "weights" / "real.safetensors").exists()
    assert (round_dir / "weights" / "extra.bin").exists()
    assert (round_dir / "weights" / "config.json").exists()
    protected = {Path(path).relative_to(root).as_posix() for path, _ in reports["round"].protected}
    assert {f"round/{name}" for name in ("evidence", "weights", "comfyops/isolated-packages",
                                        "comfyops/models", "comfyops/setup")} <= protected
    assert reports["round"].errors == []
    # a zero-filled file with a weight header is a test fake, not a model
    assert reports["round"].actions["kept-real-weight"] == 1


def test_zero_body_weight_is_test_junk_not_a_model(tmp_path: Path) -> None:
    root = tmp_path / "test-root"
    _fake_weight(root / "fakes" / "data" / "fake.safetensors", zero_body=True)
    _touch(root / "fakes" / "data" / "filler.bin", BIG)

    _reports(root)

    assert not (root / "fakes").exists()


@pytest.mark.parametrize("prune_links", [False, True])
def test_links_are_removed_as_links_only_and_targets_survive(tmp_path: Path, prune_links: bool) -> None:
    outside = tmp_path / "outside-target"
    _touch(outside / "node_modules" / "pkg" / "index.js")
    root = tmp_path / "test-root"
    # a link inside a data directory that is purged, and one inside light evidence that is kept
    _touch(root / "gone" / "data" / "data.bin", BIG)
    _touch(root / "light" / "notes.json")
    if not _make_dir_link(outside / "node_modules", root / "gone" / "data" / "node_modules"):
        pytest.skip("a directory link cannot be created on this machine")
    assert _make_dir_link(outside / "node_modules", root / "light" / "node_modules")

    reports = _reports(root, prune_links=prune_links)

    assert (outside / "node_modules" / "pkg" / "index.js").exists()
    assert not (root / "gone").exists()
    assert (root / "light" / "notes.json").exists()
    assert os.path.lexists(root / "light" / "node_modules") is not prune_links
    assert len(reports["gone"].removed_links) == 1


def test_cli_is_a_dry_run_unless_apply_is_given(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "test-root"
    _touch(root / "bt-run" / "test_a0" / "x.bin")
    monkeypatch.syspath_prepend(str(SCRIPTS))  # running the script puts its own directory there

    assert cleaner_module.main(["--work-root", str(root)]) == 0
    assert (root / "bt-run" / "test_a0" / "x.bin").exists()
    assert "演练" in capsys.readouterr().out

    assert cleaner_module.main(["--work-root", str(root), "--apply"]) == 0
    assert not (root / "bt-run").exists()
    assert "正式执行" in capsys.readouterr().out


def test_the_layout_containers_are_transparent(tmp_path: Path) -> None:
    r"""``manual\<round>`` and ``acceptance\<round>`` are the entries, not the containers around them."""
    root = tmp_path / "test-root"
    targeted = root / "manual" / "20260101-targeted"
    _touch(targeted / "bt" / "test_a0" / "x.bin")
    _touch(targeted / "pytest.log")
    _touch(root / "manual" / "20260101-old" / "bt" / "test_b0" / "y.bin")
    acceptance = root / "acceptance" / "20260101-round"
    _touch(acceptance / "data" / "shine.db", BIG)
    _touch(acceptance / "evidence" / "shot.png")
    _touch(root / "shards-20260101-000000-000" / "chunk-0.log")

    reports = _reports(root)

    assert set(reports) == {"manual/20260101-old", "manual/20260101-targeted",
                            "acceptance/20260101-round", "shards-20260101-000000-000"}
    assert not (targeted / "bt").exists()
    assert (targeted / "pytest.log").exists()
    assert not (root / "manual" / "20260101-old").exists()
    assert not (acceptance / "data").exists()
    assert (acceptance / "evidence" / "shot.png").exists()
    assert (root / "shards-20260101-000000-000" / "chunk-0.log").exists()


def test_work_root_comes_from_the_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(root_module.TEST_ROOT_ENV, raising=False)
    with pytest.raises(RuntimeError, match="test root not given"):
        root_module.work_root()

    assert root_module.work_root(tmp_path) == tmp_path.resolve()
    monkeypatch.setenv(root_module.TEST_ROOT_ENV, str(tmp_path))
    assert root_module.work_root() == tmp_path.resolve()

    with pytest.raises(RuntimeError, match="does not exist"):
        root_module.work_root(tmp_path / "missing")
    assert root_module.acceptance_dir(tmp_path, "round", "data") == tmp_path / "acceptance" / "round" / "data"


def test_scripts_never_point_at_retired_or_machine_locations() -> None:
    offenders = []
    sources = [*SCRIPTS.glob("*.py"), *SCRIPTS.glob("*.ps1"), REPO / "run_backend_shards.ps1"]
    for source in sources:
        text = source.read_text(encoding="utf-8-sig").lower()
        offenders += [f"{source.relative_to(REPO).as_posix()}: {location}"
                      for location in RETIRED_LOCATIONS if location in text]
    assert offenders == []


def test_conftest_isolates_the_data_directory_before_importing_the_app() -> None:
    """``shine.main`` builds its app at import; the pin must stay above the first ``shine`` import."""
    lines = (REPO / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8").splitlines()
    pin = next(i for i, line in enumerate(lines) if 'os.environ["SHINE_DATA_DIR"] = ' in line)
    first_shine_import = next(i for i, line in enumerate(lines)
                              if line.startswith(("from shine", "import shine")))
    assert pin < first_shine_import
    assert any("atexit.register(shutil.rmtree, os.environ[\"SHINE_DATA_DIR\"], True)" in line for line in lines)
    assert any("atexit.register(_dispose_import_time_app)" in line for line in lines)

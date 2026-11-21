from __future__ import annotations

import atexit
import ctypes
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

# Enable fast non-fsync SQLite PRAGMAs and template DB reuse during pytest runs
# (transaction rollback, WAL concurrency, and FK checks remain intact).
os.environ.setdefault("SHINE_TEST_FAST_SQLITE", "1")

import pytest
from fastapi.testclient import TestClient

from shine.config import Settings
from shine.db import Base, make_engine
from shine.main import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-fixture"

_FSCTL_SET_SPARSE = 0x000900C4
_GENERIC_WRITE = 0x40000000
_CREATE_ALWAYS = 2
_FILE_ATTRIBUTE_NORMAL = 0x80


def _mark_sparse_windows(path: Path) -> None:
    """Flag *path* as an NTFS sparse file (no administrator rights needed)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel32.DeviceIoControl.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
    ]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateFileW(
        str(path), _GENERIC_WRITE, 0, None, _CREATE_ALWAYS, _FILE_ATTRIBUTE_NORMAL, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        returned = ctypes.c_uint32(0)
        if not kernel32.DeviceIoControl(
                ctypes.c_void_p(handle), _FSCTL_SET_SPARSE, None, 0,
                None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def make_sparse_file(path: Path, size: int) -> None:
    """Create a file of *size* logical bytes without allocating the disk blocks.

    The resource scanner matches candidates on ``st_size``, so the logical size
    must be exact.  On NTFS, marking the file sparse and then ``truncate``-ing to
    the target size still allocates every cluster — a single regression run wrote
    ~70 GB of fake weights that way and filled the test drive.  Seeking past the
    end and writing the final byte is what actually keeps the interior
    unallocated; measured 20 GiB logical / 0.1 MiB on disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if size <= 0:
        path.write_bytes(b"")
        return
    if os.name == "nt":
        _mark_sparse_windows(path)
        with path.open("r+b") as handle:
            handle.seek(size - 1)
            handle.write(b"\x00")
        return
    with path.open("wb") as handle:
        handle.truncate(size)


_TEST_TIERS = ("unit", "integration", "full")
_TEST_DIMENSIONS = ("windows_only", "media", "external_service", "gpu_required")


def pytest_addoption(parser):
    group = parser.getgroup("shine-test-tiers")
    group.addoption(
        "--shine-runner-tier",
        choices=("quick", "integration", "full", "profile"),
        default=None,
        help="Apply the supported Shine test tier selection policy.",
    )
    group.addoption(
        "--shine-quick-scope",
        choices=("unit", "integration"),
        default="unit",
        help="For quick runs, optionally include the complete integration tier.",
    )
    group.addoption(
        "--shine-quick-target",
        action="append",
        default=[],
        help="Repository-relative test file to include in a quick run.",
    )
    group.addoption(
        "--shine-module",
        action="append",
        default=[],
        help="Restrict an integration run to test module names containing this value.",
    )
    group.addoption(
        "--shine-integration-target",
        action="append",
        default=[],
        help="Restrict integration tests to repository-relative files selected by diff analysis.",
    )


def pytest_collection_modifyitems(config, items):
    tier_counts = Counter()
    dimension_counts = Counter()
    defaulted_integration = 0
    defaulted_full = 0
    item_tiers = {}
    for item in items:
        own_tiers = [
            name for name in _TEST_TIERS
            if any(marker.name == name for marker in item.own_markers)
        ]
        inherited_tiers = [name for name in _TEST_TIERS if item.get_closest_marker(name)]
        if len(own_tiers) > 1 or (not own_tiers and len(inherited_tiers) > 1):
            marked_tiers = own_tiers or inherited_tiers
            raise pytest.UsageError(
                f"{item.nodeid} has multiple test tier markers: {', '.join(marked_tiers)}"
            )
        function_code = getattr(getattr(item, "function", None), "__code__", None)
        uses_synthetic_media = bool(function_code and "make_test_video_bytes" in function_code.co_names)
        if uses_synthetic_media:
            if own_tiers and own_tiers[0] != "full":
                raise pytest.UsageError(
                    f"{item.nodeid} encodes synthetic media and conflicts with its explicit {own_tiers[0]} tier."
                )
            if not own_tiers and inherited_tiers and inherited_tiers[0] == "unit":
                raise pytest.UsageError(
                    f"{item.nodeid} encodes synthetic media inside a unit-tier module; split the module marker."
                )
            if not item.get_closest_marker("media"):
                item.add_marker(pytest.mark.media)
            if not item.get_closest_marker("full"):
                item.add_marker(pytest.mark.full)
            if not own_tiers and not inherited_tiers:
                defaulted_full += 1
            tier = "full"
        elif own_tiers:
            tier = own_tiers[0]
        elif inherited_tiers:
            tier = inherited_tiers[0]
        else:
            # Unknown tests are visible in L3 until explicitly assigned to L1/L2.
            item.add_marker(pytest.mark.full)
            tier = "full"
            defaulted_full += 1

        item_tiers[id(item)] = tier
        tier_counts[tier] += 1

        for name in _TEST_DIMENSIONS:
            if item.get_closest_marker(name):
                dimension_counts[name] += 1
        if not item.get_closest_marker("windows_only"):
            for marker in item.iter_markers("skipif"):
                reason = str(marker.kwargs.get("reason", "")).casefold()
                if any(value in reason for value in ("windows", "job object", "powershell 5.1", "windows process")):
                    item.add_marker(pytest.mark.windows_only)
                    dimension_counts["windows_only"] += 1
                    break

    if sum(tier_counts.values()) != len(items):
        raise pytest.UsageError("Every collected backend test must belong to exactly one tier.")

    original_count = len(items)
    runner_tier = config.getoption("shine_runner_tier")
    root = Path(__file__).resolve().parents[2]
    target_paths = {
        value.replace("\\", "/").casefold()
        for value in config.getoption("shine_quick_target")
    }
    module_filters = [value.casefold() for value in config.getoption("shine_module")]
    integration_targets = {
        value.replace("\\", "/").casefold()
        for value in config.getoption("shine_integration_target")
    }
    selected = []
    quick_targets_deferred = 0
    for item in items:
        tier = item_tiers[id(item)]
        relative_path = Path(item.path).resolve().relative_to(root).as_posix().casefold()
        if runner_tier == "quick":
            if relative_path in target_paths and (tier == "full" or item.get_closest_marker("media")):
                quick_targets_deferred += 1
            keep = tier == "unit" or (
                config.getoption("shine_quick_scope") == "integration" and tier == "integration"
            ) or (relative_path in target_paths and tier != "full" and not item.get_closest_marker("media"))
        elif runner_tier == "integration":
            keep = tier == "integration"
            if integration_targets:
                keep = keep and relative_path in integration_targets
            if module_filters:
                module_name = Path(item.path).stem.casefold()
                keep = keep and any(value in module_name for value in module_filters)
        else:
            keep = True
        if keep:
            selected.append(item)

    integration_target_fallback = False
    if runner_tier == "integration" and integration_targets and not selected and not module_filters:
        # A mapped file may contain only L1 or L3 cases. An empty L2 run is
        # not a validation result, so widen safely to the complete L2 set.
        selected = [item for item in items if item_tiers[id(item)] == "integration"]
        integration_target_fallback = True

    selected_ids = {id(item) for item in selected}
    deselected = [item for item in items if id(item) not in selected_ids]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    config._shine_test_tier_summary = {
        "tier": runner_tier or "full (default pytest)",
        "total": original_count,
        "selected": len(selected),
        "deselected": len(deselected),
        "quick_targets_deferred": quick_targets_deferred,
        "integration_target_fallback": integration_target_fallback,
        "integration_targets": sorted(integration_targets),
        "module_filters": module_filters,
        "defaulted_integration": defaulted_integration,
        "defaulted_full": defaulted_full,
        "tiers": dict(tier_counts),
        "dimensions": dict(dimension_counts),
    }
    module_counts = Counter(
        Path(item.path).resolve().relative_to(root).as_posix()
        for item in selected
    )
    config._shine_module_case_counts = module_counts
    config._shine_module_durations = Counter()
    config._shine_module_phases = Counter()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    module_durations = getattr(item.config, "_shine_module_durations", None)
    if module_durations is not None:
        root = Path(__file__).resolve().parents[2]
        module = Path(item.path).resolve().relative_to(root).as_posix()
        module_durations[module] += report.duration
        item.config._shine_module_phases[module] += 1


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    summary = getattr(config, "_shine_test_tier_summary", None)
    if not summary:
        return
    tiers = summary["tiers"]
    dimensions = summary["dimensions"]
    terminalreporter.write_sep(
        "=",
        "Shine test-tier collection guard",
    )
    terminalreporter.write_line(
        "[shine-tier] "
        f"total={summary['total']} selected={summary['selected']} deselected={summary['deselected']} "
        f"unit={tiers.get('unit', 0)} integration={tiers.get('integration', 0)} "
        f"full={tiers.get('full', 0)} defaulted_to_integration={summary['defaulted_integration']} "
        f"defaulted_to_full={summary['defaulted_full']}"
    )
    terminalreporter.write_line(
        "[shine-tier] dimensions "
        + " ".join(f"{name}={dimensions.get(name, 0)}" for name in _TEST_DIMENSIONS)
    )
    terminalreporter.write_line(f"[shine-tier] selected test modules={len(config._shine_module_case_counts)}")
    module_durations = getattr(config, "_shine_module_durations", {})
    for module, seconds in sorted(module_durations.items(), key=lambda row: row[1], reverse=True)[:20]:
        terminalreporter.write_line(
            f"[shine-module] {seconds:.3f}s phases={config._shine_module_phases[module]} "
            f"cases={config._shine_module_case_counts[module]} {module}"
        )
    if summary["quick_targets_deferred"]:
        terminalreporter.write_line(
            f"[shine-tier] changed full/media test cases deferred to L3: {summary['quick_targets_deferred']}"
        )
    if summary["integration_target_fallback"]:
        terminalreporter.write_line(
            "[shine-tier] changed-file selection contained no L2 tests; running the complete integration tier"
        )
    if summary["deselected"]:
        if summary["tier"] == "quick":
            reason = (
                "quick policy: all unit tests plus compatible changed-module targets; media/full targets stay in L3"
            )
        elif summary["integration_targets"]:
            reason = "integration diff selection: only matching mapped files are selected"
        elif summary["module_filters"]:
            reason = "integration module filter: other integration modules are intentionally omitted"
        else:
            reason = "integration policy: full-only tests remain in L3"
        terminalreporter.write_line(f"[shine-tier] deselected reason: {reason}")


def qualified_hardware() -> dict:
    """The hardware sections of a host that meets the default variant's floors."""
    return {
        'gpus': [{'index': '0', 'uuid': 'GPU-FIXTURE', 'name': 'Contract Fixture GPU',
                  'driver_version': '0.0', 'vram_total_mb': 16384, 'vram_used_mb': 0,
                  'vram_free_mb': 16384, 'selected': True}],
        'gpu': {'status': 'OK', 'index': '0', 'uuid': 'GPU-FIXTURE',
                'name': 'Contract Fixture GPU', 'driver_version': '0.0', 'vram_mb': 16384,
                'vram_total_mb': 16384, 'vram_used_mb': 0, 'vram_free_mb': 16384},
        'cuda': {'status': 'OK', 'compute_capability': '8.6', 'compute_capability_source': 'fixture',
                 'torch': {'status': 'UNKNOWN', 'reason': 'FIXTURE',
                           'pinned_wheel': 'torch 2.7.1+cu128', 'version': None,
                           'cuda_build': None, 'arch_list': None}},
        'ram': {'status': 'OK', 'ram_mb': 65536, 'ram_available_mb': 65536, 'at_floor': True},
        'commit': {'status': 'OK', 'commit_total_mb': 131072, 'commit_available_mb': 131072,
                   'page_file_status': 'PAGE_FILE_PRESENT', 'page_file_headroom_mb': 65536},
    }


def qualified_host_facts(*args, **kwargs) -> dict:
    """A synthetic host that meets the default variant's admission thresholds.

    The server re-checks the hardware for every install plan.  A contract
    fixture has no real GPU, so it declares a capable host explicitly instead
    of letting the gate fail for the wrong reason.  This is a fixture, not
    product evidence: nothing here proves that H3 runs anywhere.
    """
    return {
        **qualified_hardware(),
        'platform': {'system': 'Windows', 'release': '11', 'version': '10.0.26200',
                     'machine': 'AMD64', 'arch': 'x64', 'is_windows': True, 'is_x64': True,
                     'windows_build': '10.0.26200'},
        'disk': {'status': 'OK', 'free_bytes': 10 ** 15, 'total_bytes': 10 ** 15,
                 'free_gb': round(10 ** 15 / 1024 ** 3, 2), 'path': 'fixture',
                 'at_floor': True, 'runtime_floor_gb': 5},
        'paths': {'data_dir': {'path': 'fixture', 'status': 'OK', 'writable': True},
                  'deployment_dir': {'path': 'fixture', 'status': 'OK', 'writable': True},
                  'issues': []},
        'evidence': {'probed_at': '2026-01-01T00:00:00Z', 'failures': [], 'sources': {},
                     'selected_gpu_uuid': 'GPU-FIXTURE', 'multi_gpu_memory_is_summed': False},
    }


@pytest.fixture
def fake_deployment_host(monkeypatch):
    """Contract fixtures have a non-system volume with ample simulated capacity.

    No multi-GB inputs are downloaded here. Physical capacity/drive-policy
    cases override these ports explicitly; real installation acceptance is separate.
    """
    from shine.comfyops import host_env, storage
    monkeypatch.setattr(storage, 'on_system_drive', lambda _: False)
    monkeypatch.setattr(storage, 'disk_info', lambda _: {
        'free_bytes': 10**15, 'total_bytes': 10**15, 'free_gb': round(10**15 / 1024**3, 2),
    })
    # The server re-checks the hardware for every install plan.  A contract
    # fixture has no real GPU, so the hardware sections are declared while
    # the real paths and disk of the fixture stay untouched.
    probed = host_env.probe_host_facts

    def declared_host_facts(*args, **kwargs):
        facts = probed(*args, **kwargs)
        facts.update(qualified_hardware())
        # These tests exercise plans for the product's supported Windows host.
        # Keep real fixture paths and disk probes, while making the simulated
        # host platform explicit so OS qualification is tested independently
        # from the machine running pytest.
        facts['platform'] = qualified_host_facts()['platform']
        facts['evidence'] = {**(facts.get('evidence') or {}), 'failures': []}
        return facts

    monkeypatch.setattr(host_env, 'probe_host_facts', declared_host_facts)

COMFY_GRAPH = {
    "1": {
        "class_type": "H3BaseSampler",
        "inputs": {
            "positive_prompt": "",
            "seed": 0,
            "duration": 6.0,
            "reference_picture_1": "",
            "reference_subject_1": "",
            "reference_subject_2": "",
        },
    },
    "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
}

# A real Ref2VA profile maps every reference family the shot can bind.  A
# fixture that only maps a bare picture cannot actually dispatch the shots the
# director package freezes, so the package readiness correctly refuses it.
COMFY_PARAM_MAP = {
    "positive_prompt": "1.inputs.positive_prompt",
    "seed": "1.inputs.seed",
    "duration": "1.inputs.duration",
    "reference_picture_1": "1.inputs.reference_picture_1",
    "reference_subject_1": "1.inputs.reference_subject_1",
    "reference_subject_2": "1.inputs.reference_subject_2",
}


def ffmpeg_executable() -> str | None:
    """Return a usable FFmpeg binary for offline media tests.

    CI installs FFmpeg system wide; Windows developers may not have it, so the
    bundled ``imageio-ffmpeg`` binary is used as a fallback.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover - the binary is optional
        return None


def ffprobe_executable() -> str | None:
    return shutil.which("ffprobe")


@lru_cache(maxsize=16)
def make_test_video_bytes(
    *,
    duration_s: float = 6.0,
    width: int = 768,
    height: int = 1344,
    fps: int = 24,
    with_audio: bool = True,
) -> bytes:
    """Render a real, decodable MP4 for software tests only.

    The produced media is a synthetic test pattern. It is never used as a
    character, location or prop master asset.
    """
    cache_dir_raw = os.getenv("SHINE_TEST_MEDIA_CACHE_DIR", "").strip()
    cache_file: Path | None = None
    if cache_dir_raw:
        cache_dir = Path(cache_dir_raw)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / (
            f"test-media-{width}x{height}-{fps}fps-{duration_s:.2f}s-"
            f"{'a' if with_audio else 'noaudio'}.mp4"
        )
        if cache_file.is_file():
            return cache_file.read_bytes()
    executable = ffmpeg_executable()
    if not executable:
        raise RuntimeError("FFmpeg is required to build the synthetic test media")
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "test-media.mp4"
        command = [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate={fps}:duration={duration_s}",
        ]
        if with_audio:
            command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_s}"]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if with_audio:
            command += ["-c:a", "aac", "-shortest"]
        command.append(str(target))
        completed = subprocess.run(command, capture_output=True, timeout=300, check=False)
        if completed.returncode != 0 or not target.is_file():
            raise RuntimeError(
                "FFmpeg could not build the synthetic test media: "
                + completed.stderr.decode("utf-8", errors="replace")[:400]
            )
        payload = target.read_bytes()
        if cache_file is not None:
            tmp_cache = cache_file.with_name(f".{cache_file.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
            try:
                tmp_cache.write_bytes(payload)
                os.replace(tmp_cache, cache_file)
            except OSError:
                tmp_cache.unlink(missing_ok=True)
        return payload


@lru_cache(maxsize=1)
def _template_db_dir() -> Path:
    folder = Path(tempfile.mkdtemp(prefix="shine-test-tpl-"))
    atexit.register(lambda: shutil.rmtree(folder, ignore_errors=True))
    return folder


@lru_cache(maxsize=1)
def _template_shine_db() -> Path:
    path = _template_db_dir() / "template-shine.sqlite3"
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.exec_driver_sql("PRAGMA journal_mode=DELETE")
    engine.dispose()
    return path


@lru_cache(maxsize=1)
def _template_m4_db() -> Path:
    from shine.manual4.storage.db import make_m4_engine
    from shine.manual4.storage.migrations import ensure_m4_schema

    path = _template_db_dir() / "template-m4.sqlite"
    engine = make_m4_engine(f"sqlite:///{path.as_posix()}")
    ensure_m4_schema(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.exec_driver_sql("PRAGMA journal_mode=DELETE")
    engine.dispose()
    return path


def style_payload() -> dict[str, str]:
    return {
        "photography_language": "平视中近景，稳定缓推",
        "shot_blocking": "对话保持视线轴线",
        "color_palette": "青灰环境与温暖肤色",
        "lighting_tone": "单侧主光，保留阴影层次",
        "lens_texture": "自然浅景深，轻颗粒",
        "editing_rhythm": "对白停顿后切反应镜头",
        "sound_performance": "普通话自然语速，环境声克制",
    }


def make_studio(tmp_path: Path, *, max_upload_bytes: int = 25 * 1024 * 1024):
    data_dir = tmp_path / "本地素材"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "shine.sqlite3"
    shutil.copyfile(_template_shine_db(), db_path)
    shutil.copyfile(_template_m4_db(), data_dir / "manual4.sqlite")
    database_url = f"sqlite:///{db_path.as_posix()}"
    # 测试不得读真机渲染器：默认 8188 可能被操作者正在跑的生产占用（真实节点表
    # 与测试节点不同），登记时的尽力而为活体校验会把真机事实写进测试台账。
    # 取一个刚释放的环回端口（几乎必然无人监听），需要渲染器的测试显式传 fake。
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    settings = Settings(
        data_dir=data_dir,
        database_url=database_url,
        max_upload_bytes=max_upload_bytes,
        secret_store_dir=data_dir / "secrets",
        worker_offline_after_seconds=30,
        comfyui_base_url=f"http://127.0.0.1:{closed_port}",
        # A synthetic ComfyUI root has no real node packs on disk.  The fixture
        # says so explicitly, so the gate reports the missing packs instead of
        # refusing every configuration contract test.
        node_pack_gate="diagnose",
    )
    app = create_app(settings=settings, database_url=database_url, asset_root=data_dir)
    return app, app.state.session_factory, settings


@pytest.fixture
def studio(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        yield client, factory, tmp_path, settings
    app.state.engine.dispose()


@pytest.fixture(autouse=True)
def isolated_deployment_env(tmp_path, monkeypatch):
    """Every test writes its own .env.

    The deployment writer targets the repository-root .env by default.  That one
    file is shared by every parallel shard — and with the operator's own
    installation — so two shards overwrote each other's settings (and the
    operator's .env).  Tests that need another path monkeypatch it themselves.
    """
    from shine.comfyops import deployment

    monkeypatch.setattr(deployment, "ENV_PATH", tmp_path / "isolated-env")


@pytest.fixture
def api(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        yield client, factory, settings
    app.state.engine.dispose()


def create_project(client: TestClient, title: str = "雾港") -> str:
    response = client.post("/api/v1/projects", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def freeze_style(client: TestClient, project_id: str) -> dict:
    draft = client.put(f"/api/v1/projects/{project_id}/style/draft", json=style_payload())
    assert draft.status_code == 200, draft.text
    response = client.post(f"/api/v1/projects/{project_id}/style/freeze", json={})
    assert response.status_code == 201, response.text
    return response.json()


def upload_asset(
    client: TestClient,
    project_id: str,
    content: bytes = PNG,
    filename: str = "角色.png",
    *,
    kind: str = "image",
    role: str | None = None,
    entity_id: str | None = None,
    tags: str | None = None,
    content_type: str = "image/png",
) -> dict:
    data = {"asset_kind": kind}
    if role:
        data["role"] = role
    if entity_id:
        data["entity_id"] = entity_id
    if tags:
        data["tags"] = tags
    response = client.post(
        f"/api/v1/projects/{project_id}/assets",
        data=data,
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def asset_decision(client, asset_id, action):
    import hashlib
    from dataclasses import replace

    token = "isolated-legacy-asset-test-operator-credential"
    client.app.state.settings = replace(
        client.app.state.settings,
        operator_id="isolated-asset-test-operator",
        operator_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
    )
    headers = {"Authorization": "Bearer " + token}
    grant = client.post(
        "/api/v1/preparation/approval-grants",
        headers=headers,
        json={"action": "asset_" + action, "target_id": asset_id},
    )
    assert grant.status_code == 200, grant.text
    return client.post(
        f"/api/v1/assets/{asset_id}/{action}",
        headers=headers,
        json={k: grant.json()[k] for k in ("grant_id", "snapshot")}
        | {"offline": True, "evidence": "Explicit isolated legacy test operator"},
    )


def approve_lock(client: TestClient, asset_id: str) -> None:
    for action in ("approve", "lock"):
        result = asset_decision(client, asset_id, action)
        assert result.status_code == 200, result.text


def shot_payload(
    style_id: str,
    *,
    title: str = "对话镜头",
    beat: str = "两人交换线索",
    asset_ids=None,
    characters=None,
    dialogue=None,
    duration: float = 6,
    continuity=None,
    bindings=None,
) -> dict:
    return {
        "name": title,
        "spec": {
            "schema_version": "1.0",
            "story_beat": beat,
            "duration_target_s": duration,
            "shot_priority": "B",
            "style_version_id": style_id,
            "camera": {"shot_size": "medium"},
            "characters": characters or [],
            "required_asset_ids": asset_ids or [],
            "dialogue": dialogue or [],
            "sound": {},
            "continuity": continuity or {},
            "locked_story_facts": [],
        },
        "bindings": bindings or [],
    }


def create_shot(client: TestClient, project_id: str, style_id: str, **kwargs) -> dict:
    body = shot_payload(style_id, **kwargs)
    response = client.post(f"/api/v1/projects/{project_id}/shots", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _approve_lock_keeping_operator(client: TestClient, asset_id: str) -> None:
    """Approve and lock a fixture master without changing the caller's operator.

    The legacy grant path rewrites ``app.state.settings`` with its own operator
    credential.  A caller that later uses the real operator token would then be
    refused, so the fixture restores the settings it borrowed.
    """
    saved = client.app.state.settings
    try:
        approve_lock(client, asset_id)
    finally:
        client.app.state.settings = saved


def create_referenced_shot(client: TestClient, project_id: str, style_id: str, *,
                           character: str = "REF_A", **kwargs) -> dict:
    """A shot carrying one approved subject reference.

    The project invariant is Ref2VA-only, so a provider-level fixture that
    really submits a shot must bind a master instead of creating a text-only
    shot.  The content differs per call because the same bytes would be the
    same approved asset and could not be confirmed twice.
    """
    asset = upload_asset(
        client, project_id, content=PNG + uuid4().hex.encode("utf-8"),
        filename=f"{character}.png", role="character", entity_id=character,
    )
    _approve_lock_keeping_operator(client, asset["id"])
    kwargs.setdefault("characters", [character])
    kwargs.setdefault("asset_ids", [asset["id"]])
    kwargs.setdefault("bindings", [{
        "asset_id": asset["id"], "use_as": "subject", "role": character, "ordinal": 0,
    }])
    return create_shot(client, project_id, style_id, **kwargs)


def create_job(client: TestClient, project_id: str, shot_id: str, **kwargs) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/jobs", json={"shot_id": shot_id, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def register_workflow_profile(client: TestClient, **overrides) -> dict:
    body = {
        # A workflow profile name is unique per database.  `id(overrides)` was
        # used here and can repeat once a dict is freed and a new one lands on
        # the same address, which made a long test process fail with
        # "a workflow profile with this name already exists" depending on
        # allocation order.  A uuid is what the test actually means: a fresh name.
        "name": overrides.pop("name", f"test-profile-{uuid4().hex[:12]}"),
        # The project is Ref2VA-only, so the default profile is the real
        # reference-driven one.  A test that needs another mode asks for it
        # explicitly, exactly like a real project must.
        "mode": "Ref2VA",
        "graph": overrides.pop("graph", COMFY_GRAPH),
        "parameter_map": overrides.pop("parameter_map", COMFY_PARAM_MAP),
        "required_nodes": overrides.pop("required_nodes", ["H3BaseSampler"]),
    }
    body.update(overrides)
    response = client.post("/api/v1/workflow-profiles", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def ensure_shot_references(client: TestClient, project_id: str, shot_id: str) -> None:
    """Bind one approved subject master per character a real job would submit.

    The project invariant is Ref2VA-only: a ComfyUI job without a reference is
    refused by name.  Provider-level fixtures still exercise their own subject
    (leases, recovery, QC), so this helper supplies the reference they would
    carry in a real project instead of weakening the product rule.
    """
    bindings = client.get(f"/api/v1/shots/{shot_id}/bindings").json()
    if bindings:
        return
    shots = client.get(f"/api/v1/projects/{project_id}/shots").json()
    shot = next((row for row in shots if row["id"] == shot_id), None)
    characters = list(((shot or {}).get("spec") or {}).get("characters") or [])
    # Reuse an approved master first: creating one would change the project's
    # asset context and invalidate crew artifacts a later step must read.
    approved = [row for row in client.get(f"/api/v1/projects/{project_id}/assets").json()
                if row.get("approved") and row.get("locked")]
    rows = []

    def _master(character: str) -> dict:
        if not characters and approved:
            # A shot that names nobody still needs one reference; any approved
            # master keeps the asset context unchanged.
            return approved[0]
        match = next((row for row in approved if row.get("entity_id") == character), None)
        if match is not None:
            return match
        created = upload_asset(
            client, project_id, content=PNG + uuid4().hex.encode("utf-8"),
            filename=f"{character}.png",
            role="character" if characters else "scene", entity_id=character,
        )
        _approve_lock_keeping_operator(client, created["id"])
        approved.append(created)
        return created

    for index, character in enumerate(characters or ["参考场景"]):
        asset = _master(character)
        rows.append({"asset_id": asset["id"], "use_as": "subject",
                     "role": character, "ordinal": index})
    response = client.put(
        f"/api/v1/shots/{shot_id}/bindings",
        headers={"X-Shine-Local-Action": "1"},
        json={"bindings": rows},
    )
    assert response.status_code == 200, response.text


def create_comfy_job(
    client: TestClient,
    project_id: str,
    shot_id: str,
    *,
    profile: dict | None = None,
    **kwargs,
) -> dict:
    ensure_shot_references(client, project_id, shot_id)
    if profile is None:
        profile = register_workflow_profile(client)
    return create_job(
        client,
        project_id,
        shot_id,
        provider="comfyui",
        workflow_profile_id=profile["id"],
        **kwargs,
    )


def stamp_brief_basis(factory, item_id: str) -> None:
    """Bind a preset brief to the current visual facts so reuse is provable.

    A brief without a basis is stale history by contract (the frozen style or
    world may have changed since it was written); tests that preset a brief to
    exercise the render path must stamp the basis the producer would write.
    """
    from shine.materialprep import brief as brief_module
    from shine.materialprep.models import MaterialItem
    with factory.begin() as db:
        row = db.get(MaterialItem, item_id)
        row.payload = {**row.payload,
                       "brief_basis": brief_module._load_context(factory, item_id)["brief_basis"]}

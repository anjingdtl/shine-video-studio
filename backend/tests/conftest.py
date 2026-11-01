from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-fixture"


@pytest.fixture
def fake_deployment_host(monkeypatch):
    """Contract fixtures have a non-system volume with ample simulated capacity.

    No multi-GB inputs are downloaded here. Physical capacity/drive-policy
    cases override these ports explicitly; real installation acceptance is separate.
    """
    from shine.comfyops import storage
    monkeypatch.setattr(storage, 'on_system_drive', lambda _: False)
    monkeypatch.setattr(storage, 'disk_info', lambda _: {
        'free_bytes': 10**15, 'total_bytes': 10**15, 'free_gb': round(10**15 / 1024**3, 2),
    })

COMFY_GRAPH = {
    "1": {
        "class_type": "H3BaseSampler",
        "inputs": {
            "positive_prompt": "",
            "seed": 0,
            "duration": 6.0,
            "reference_picture_1": "",
        },
    },
    "2": {"class_type": "SaveVideo", "inputs": {"images": ["1", 0]}},
}

COMFY_PARAM_MAP = {
    "positive_prompt": "1.inputs.positive_prompt",
    "seed": "1.inputs.seed",
    "duration": "1.inputs.duration",
    "reference_picture_1": "1.inputs.reference_picture_1",
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
        return target.read_bytes()


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
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(
        data_dir=data_dir,
        database_url=database_url,
        max_upload_bytes=max_upload_bytes,
        secret_store_dir=data_dir / "secrets",
        worker_offline_after_seconds=30,
    )
    app = create_app(settings=settings, database_url=database_url, asset_root=data_dir)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    return app, factory, settings


@pytest.fixture
def studio(tmp_path):
    app, factory, settings = make_studio(tmp_path)
    with TestClient(app) as client:
        yield client, factory, tmp_path, settings
    app.state.engine.dispose()


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


def create_job(client: TestClient, project_id: str, shot_id: str, **kwargs) -> dict:
    response = client.post(f"/api/v1/projects/{project_id}/jobs", json={"shot_id": shot_id, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


def register_workflow_profile(client: TestClient, **overrides) -> dict:
    body = {
        "name": overrides.pop("name", f"test-profile-{id(overrides)}"),
        "mode": "T2VA",
        "graph": overrides.pop("graph", COMFY_GRAPH),
        "parameter_map": overrides.pop("parameter_map", COMFY_PARAM_MAP),
        "required_nodes": overrides.pop("required_nodes", ["H3BaseSampler"]),
    }
    body.update(overrides)
    response = client.post("/api/v1/workflow-profiles", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def create_comfy_job(
    client: TestClient,
    project_id: str,
    shot_id: str,
    *,
    profile: dict | None = None,
    **kwargs,
) -> dict:
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

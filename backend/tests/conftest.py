from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-fixture"


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


def approve_lock(client: TestClient, asset_id: str) -> None:
    approved = client.post(f"/api/v1/assets/{asset_id}/approve")
    assert approved.status_code == 200, approved.text
    locked = client.post(f"/api/v1/assets/{asset_id}/lock")
    assert locked.status_code == 200, locked.text


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

from __future__ import annotations

import hashlib

from sqlalchemy import select

from shine.asset_center.models import LibraryRenderTask
from shine.media.artifacts import store_artifact
from shine.media.output_layout import store_library_asset
from shine.media.video_naming import clip_filename
from shine.models import AppSetting, Asset, Job, MediaArtifact, Project
from shine.security import sha256_bytes
from tests.conftest import PNG, create_project, create_shot, freeze_style, upload_asset

VIDEO = b"legacy-reel-bytes"
LOCAL = {"x-shine-local-action": "1"}


def _artifact(factory, project_id: str, storage_path: str, content: bytes) -> str:
    with factory.begin() as db:
        row = MediaArtifact(
            project_id=project_id,
            artifact_kind="delivery",
            storage_path=storage_path,
            sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            content_type="video/mp4",
            provenance={"clips": []},
        )
        db.add(row)
        db.flush()
        return row.id


def test_settings_page_reads_the_current_layout(api):
    client, _factory, settings = api
    create_project(client, "雾港夜账")
    response = client.get("/api/v1/settings/output-directory")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["root"] == str(settings.data_dir)
    assert body["custom"] is False
    assert body["layout"] == {
        "material": "素材", "video": "视频", "voice": "音色", "material_center": "素材中心",
    }
    assert [row["folder"] for row in body["projects"]] == ["雾港夜账"]


def test_uploaded_master_is_named_project_type_name(api):
    client, factory, settings = api
    project_id = create_project(client, "雾港夜账")
    asset = upload_asset(client, project_id, PNG, "角色.png", entity_id="易千寒")

    with factory() as db:
        row = db.get(Asset, asset["id"])
        assert row.storage_path == "雾港夜账/素材/雾港夜账_素材_易千寒.png"
        assert row.filename == "角色.png"
        assert (settings.data_dir / row.storage_path).read_bytes() == PNG


def test_changing_the_root_migrates_files_and_switches_the_ledger(api, tmp_path):
    client, factory, settings = api
    project_id = create_project(client, "雾港夜账")
    asset = upload_asset(client, project_id, PNG, "角色.png", entity_id="易千寒")
    stored, _digest, _size = store_artifact(
        settings.data_dir, project_id, "delivery", VIDEO, "video/mp4",
        filename="旧成片.mp4", subdir="delivery/current",
    )
    artifact_id = _artifact(factory, project_id, stored, VIDEO)
    new_root = tmp_path / "作品输出"

    response = client.put(
        "/api/v1/settings/output-directory", json={"path": str(new_root)}, headers=LOCAL
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["moved"] >= 2
    assert payload["root"] == str(new_root.resolve())

    with factory() as db:
        row = db.get(Asset, asset["id"])
        assert row.storage_path == "雾港夜账/素材/雾港夜账_素材_易千寒.png"
        assert (new_root / row.storage_path).read_bytes() == PNG
        artifact = db.get(MediaArtifact, artifact_id)
        assert artifact.storage_path.startswith("雾港夜账/视频/")
        assert (new_root / artifact.storage_path).read_bytes() == VIDEO
        setting = db.get(AppSetting, "output-directory")
        assert setting is not None and setting.value["root"] == str(new_root.resolve())

    assert not (settings.data_dir / stored).exists()
    media = client.get(f"/api/v1/deliveries/{artifact_id}/media")
    assert media.status_code == 200, media.text
    assert media.content == VIDEO


def test_resetting_to_the_default_root_moves_files_back(api, tmp_path):
    client, factory, settings = api
    project_id = create_project(client, "雾港夜账")
    upload_asset(client, project_id, PNG, "角色.png", entity_id="易千寒")
    custom = tmp_path / "作品输出"
    assert client.put(
        "/api/v1/settings/output-directory", json={"path": str(custom)}, headers=LOCAL
    ).status_code == 200

    response = client.put(
        "/api/v1/settings/output-directory", json={"path": None}, headers=LOCAL
    )
    assert response.status_code == 200, response.text
    with factory() as db:
        assert db.get(AppSetting, "output-directory") is None
        row = db.scalars(select(Asset)).one()
        assert (settings.data_dir / row.storage_path).is_file()
        assert not (custom / row.storage_path).exists()


def test_a_legacy_candidate_moves_and_keeps_its_payload_hash_consistent(api, tmp_path):
    from shine.comfy.workflow import graph_hash
    from shine.comfyops.models import CandidateAsset

    client, factory, settings = api
    project_id = create_project(client, "雾港夜账")
    digest = sha256_bytes(PNG)
    legacy = settings.data_dir / "assets" / digest[:2] / f"{digest}.png"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(PNG)
    payload = {
        "family": "character", "entity_id": "易千寒", "kind": "image",
        "filename": "角色.png", "content_type": "image/png",
        "content_sha256": digest, "byte_size": len(PNG),
        "storage_path": f"assets/{digest[:2]}/{digest}.png",
        "rights_id": None, "provenance": {"is_mock": False}, "parent_id": None,
        "style_id": None, "media": {}, "qc": None,
    }
    with factory.begin() as db:
        row = CandidateAsset(
            project_id=project_id, payload=payload, sha256=graph_hash(payload), status="GENERATED"
        )
        db.add(row)
        db.flush()
        candidate_id = row.id

    response = client.put(
        "/api/v1/settings/output-directory", json={"path": str(tmp_path / "作品输出")}, headers=LOCAL
    )
    assert response.status_code == 200, response.text
    with factory() as db:
        row = db.get(CandidateAsset, candidate_id)
        assert row.payload["storage_path"] == "雾港夜账/素材/雾港夜账_素材_角色.png"
        assert row.payload["filename"] == "角色.png"
        assert row.sha256 == graph_hash(row.payload)
    assert not legacy.exists()


def test_migration_refuses_while_a_material_task_is_pending(api, tmp_path):
    client, factory, _settings = api
    create_project(client, "雾港夜账")
    with factory.begin() as db:
        db.add(LibraryRenderTask(
            idempotency_key="pending-task", request_hash="0" * 64,
            kind="image", mode="IMAGE", payload={}, status="QUEUED",
        ))
    response = client.put(
        "/api/v1/settings/output-directory", json={"path": str(tmp_path / "作品输出")},
        headers=LOCAL,
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "OUTPUT_DIRECTORY_BUSY"
    assert detail["blockers"]


def test_a_tampered_source_stops_the_migration_before_the_switch(api, tmp_path):
    client, factory, settings = api
    project_id = create_project(client, "雾港夜账")
    asset = upload_asset(client, project_id, PNG, "角色.png", entity_id="易千寒")
    with factory() as db:
        row = db.get(Asset, asset["id"])
        path = settings.data_dir / row.storage_path
    path.write_bytes(b"changed-after-the-ledger")
    new_root = tmp_path / "作品输出"

    response = client.put(
        "/api/v1/settings/output-directory", json={"path": str(new_root)}, headers=LOCAL
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "OUTPUT_DIRECTORY_SOURCE_INVALID"
    with factory() as db:
        assert db.get(AppSetting, "output-directory") is None
        row = db.get(Asset, asset["id"])
        assert row.storage_path == "雾港夜账/素材/雾港夜账_素材_易千寒.png"
        assert (settings.data_dir / row.storage_path).exists()
    # The failed attempt released its migration lock: the workbench reads and
    # accepts new work again instead of staying frozen for the stale window.
    assert client.get("/api/v1/settings/output-directory").json()["migration"] is None


def test_library_outputs_go_to_the_material_center(api):
    _client, factory, settings = api
    with factory() as db:
        path, name = store_library_asset(
            db, settings, PNG, sha256_bytes(PNG),
            kind="image", base="海报", extension=".png",
        )
    assert path == "素材中心/素材/素材中心_素材_海报.png"
    assert name == "素材中心_素材_海报.png"
    assert (settings.data_dir / path).read_bytes() == PNG


def test_clip_name_carries_project_and_type(studio):
    client, factory, _tmp_path, _settings = studio
    project_id = create_project(client, "雾港夜账")
    style = freeze_style(client, project_id)
    shot = create_shot(client, project_id, style["id"])

    with factory.begin() as db:
        job = Job(
            project_id=project_id, shot_id=shot["id"], shot_revision=1,
            style_version_id=style["id"], request={}, idempotency_key="name-job",
            status="PASSED",
        )
        db.add(job)
        db.flush()
        job_id = job.id
    with factory() as db:
        project = db.get(Project, project_id)
        assert clip_filename(db, project, db.get(Job, job_id)) == "雾港夜账_视频_001镜_未分集.mp4"


def test_missing_sources_are_reported_and_skipped_not_fatal(api, tmp_path):
    """历史缺文件不挡迁移：具名报告、路径不变，存在的文件仍逐字节核验。"""
    client, factory, settings = api
    project_id = create_project(client, "雾港夜账")
    asset = upload_asset(client, project_id, PNG, "角色.png", entity_id="易千寒")
    gone = upload_asset(client, project_id, PNG + b"distinct-bytes", "旧道具.png", entity_id="旧道具")
    with factory() as db:
        missing_path = db.get(Asset, gone["id"]).storage_path
    (settings.data_dir / missing_path).unlink()
    new_root = tmp_path / "作品输出"

    response = client.put(
        "/api/v1/settings/output-directory", json={"path": str(new_root)}, headers=LOCAL
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["missing_count"] == 1
    assert payload["missing"][0]["path"] == missing_path
    assert payload["moved"] >= 1

    with factory() as db:
        row = db.get(Asset, asset["id"])
        assert (new_root / row.storage_path).read_bytes() == PNG
        kept = db.get(Asset, gone["id"])
        assert kept.storage_path == missing_path

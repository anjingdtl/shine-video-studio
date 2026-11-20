"""P2 acceptance tests: unified Public Asset Center, task queuing, cross-line auth, backfill & 409 guards."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from shine.asset_center import service as ac_service
from shine.asset_center.models import (
    LibraryAsset,
    LibraryProjectLink,
    LibraryRenderTask,
    LibraryRights,
    LibraryVersion,
)
from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app
from shine.manual4.storage.models import M4AssetVersion, M4ShotAssetBinding
from shine.media.output_layout import store_library_asset
from shine.models import Asset, ShotAssetBinding

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _png_bytes(color: tuple[int, int, int] = (40, 130, 210), size: tuple[int, int] = (64, 64)) -> bytes:
    w, h = size
    row = b"\x00" + bytes(color) * w
    raw = row * h

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def _setup_app(tmp_path: Path, *, with_image_recipe: bool = True):
    db_path = tmp_path / "shine.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(
        data_dir=tmp_path,
        database_url=database_url,
        node_pack_gate="diagnose",
        render_provider="comfyui" if with_image_recipe else "mock",
    )
    if with_image_recipe:
        comfy_dir = tmp_path / "shared_comfy"
        (comfy_dir / "models" / "diffusion_models").mkdir(parents=True, exist_ok=True)
        (comfy_dir / "models" / "text_encoders").mkdir(parents=True, exist_ok=True)
        (comfy_dir / "models" / "vae").mkdir(parents=True, exist_ok=True)
        diff = comfy_dir / "models" / "diffusion_models" / "qwen_image_2512_fp8_e4m3fn.safetensors"
        te = comfy_dir / "models" / "text_encoders" / "qwen_2.5_vl_7b_fp8_scaled.safetensors"
        vae = comfy_dir / "models" / "vae" / "qwen_image_vae.safetensors"
        for p in (diff, te, vae):
            p.write_bytes(b"stub-model-weights")
        recipe_path = settings.comfyops_dir / "setup" / "managed_recipe.json"
        recipe_path.parent.mkdir(parents=True, exist_ok=True)
        recipe_path.write_text(
            json.dumps(
                {
                    "comfy_dir": str(comfy_dir),
                    "python_executable": str(tmp_path / "python.exe"),
                    "models": {
                        "image": [
                            {"id": "img-diff", "path": str(diff)},
                            {"id": "img-te", "path": str(te)},
                            {"id": "img-vae", "path": str(vae)},
                        ]
                    },
                    "installed_node_packs": {},
                }
            ),
            encoding="utf-8",
        )
    app = create_app(settings=settings, database_url=database_url, asset_root=tmp_path)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    return app, settings, factory


def _seed_storyboard(client: TestClient, pid: str) -> tuple[dict, str]:
    raw_text = (
        "第1集：夜巷相遇（目标总时长：5s）\n"
        "第1镜（5s）：林晚撑伞站在雨夜窄巷。\n"
        "人物：林晚\n"
        "场景：雨夜窄巷\n"
    )
    imp = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=LOCAL_ACTION_HEADERS,
        data={"text": raw_text, "filename": "plan.txt"},
    )
    assert imp.status_code == 201, imp.text
    conf = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=LOCAL_ACTION_HEADERS,
        json={"expected_revision": imp.json()["draft"]["revision"]},
    )
    assert conf.status_code == 200, conf.text
    mats = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    reqs = {(r["family"], r["entity_name"]): r for r in mats["requirements"]}
    shot_id = mats["shots"][0]["id"]
    return reqs, shot_id


def test_p2_a05_single_library_task_queue_and_sync_promotion(tmp_path: Path, monkeypatch):
    app, settings, factory = _setup_app(tmp_path, with_image_recipe=True)
    monkeypatch.setattr(
        "shine.foundation.materials.check_capability",
        lambda *_args, **_kwargs: {"capability": "image", "status": "ready", "blockers": []},
    )
    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "A05统一出图队列"},
        ).json()["id"]
        reqs, _shot_id = _seed_storyboard(client, pid)
        req_id = reqs[("character", "林晚")]["id"]

        gen = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/{req_id}/select-or-generate",
            headers=LOCAL_ACTION_HEADERS,
            json={"mode": "generate", "prompt": "lin wan raincoat portrait", "seed": 7},
        )
        assert gen.status_code == 200, gen.text
        payload = gen.json()
        task_id = payload["task_id"]
        assert payload["status"] == "QUEUED"
        assert payload["asset"] is None
        assert payload["state"]["requirements"][0]["pending_task_id"] or any(
            r["pending_task_id"] == task_id for r in payload["state"]["requirements"]
        )

        # Idempotent retry with identical prompt & seed returns same LibraryRenderTask
        gen_dup = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/{req_id}/select-or-generate",
            headers=LOCAL_ACTION_HEADERS,
            json={"mode": "generate", "prompt": "lin wan raincoat portrait", "seed": 7},
        )
        assert gen_dup.status_code == 200
        assert gen_dup.json()["task_id"] == task_id

        with factory() as p_db:
            assert p_db.scalar(select(func.count(LibraryRenderTask.id))) == 1
            img = _png_bytes((20, 200, 90))
            digest = hashlib.sha256(img).hexdigest()
            rel, _ = store_library_asset(p_db, settings, img, digest, kind="image", base="qwen_linwan")
            rights = LibraryRights(
                source_type="qwen_t2i",
                license_kind="QWEN_RESEARCH_NON_COMMERCIAL",
                scope="asset",
                statement="Generated by Qwen",
                subject="林晚",
                allowed_project_ids=[pid],
                permitted_uses=["research"],
                commercial_allowed=False,
                status="DECLARED",
                recorded_actor="worker",
            )
            p_db.add(rights)
            p_db.flush()
            la = LibraryAsset(title="林晚", kind="image", category="character", created_by="worker")
            p_db.add(la)
            p_db.flush()
            lv = LibraryVersion(
                library_asset_id=la.id,
                ordinal=1,
                filename="qwen_linwan.png",
                kind="image",
                content_type="image/png",
                content_sha256=digest,
                storage_path=rel,
                byte_size=len(img),
                width=64,
                height=64,
                source_mode="T2I",
                rights_id=rights.id,
                status="CANDIDATE",
                is_mock=False,
                created_by="worker",
            )
            p_db.add(lv)
            p_db.flush()
            task_row = p_db.get(LibraryRenderTask, task_id)
            assert task_row is not None
            task_row.status = "CANDIDATE_READY"
            task_row.result_version_id = lv.id
            p_db.commit()
            lv_id = lv.id

        # Polling task or fetching state syncs LibraryVersion -> M4AssetVersion once
        task_poll = client.get(f"/api/v1/manual4/projects/{pid}/materials/tasks/{task_id}")
        assert task_poll.status_code == 200
        assert task_poll.json()["status"] == "CANDIDATE_READY"

        state = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
        synced_req = next(r for r in state["requirements"] if r["id"] == req_id)
        assert synced_req["pending_task_id"] is None
        assert len(synced_req["candidates"]) == 1
        cand = synced_req["candidates"][0]
        assert cand["library_version_id"] == lv_id
        assert cand["status"] == "candidate"

        # Promote through unified Public Asset Center + M4
        prom = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{cand['id']}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={"requirement_id": req_id},
        )
        assert prom.status_code == 200, prom.text
        assert prom.json()["asset"]["status"] == "promoted"
        assert prom.json()["asset"]["rights_status"] == "APPROVED"

        with factory() as p_db:
            lv_row = p_db.get(LibraryVersion, lv_id)
            assert lv_row is not None and lv_row.status == "PROMOTED"
            rights_row = p_db.get(LibraryRights, lv_row.rights_id)
            assert rights_row is not None and rights_row.status == "APPROVED"

        media = client.get(f"/api/v1/manual4/projects/{pid}/assets/{cand['id']}/media")
        assert media.status_code == 200
        assert hashlib.sha256(media.content).hexdigest() == digest


def test_p2_a06_cross_line_shared_library_and_director7_409_guards(tmp_path: Path):
    app, settings, factory = _setup_app(tmp_path, with_image_recipe=False)
    with TestClient(app) as client:
        # 1. Create both a director7 project and two manual4 projects
        d7 = client.post("/api/v1/projects", headers=LOCAL_ACTION_HEADERS, json={"title": "D7测试"})
        assert d7.status_code in {200, 201}, d7.text
        d7_pid = d7.json()["id"]

        m4_1 = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "M4项目甲"},
        ).json()["id"]
        m4_2 = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "M4项目乙"},
        ).json()["id"]

        reqs1, _shot1 = _seed_storyboard(client, m4_1)
        reqs2, shot2 = _seed_storyboard(client, m4_2)

        # Verify generating without image capability returns 409/503 without blocking upload
        no_gen = client.post(
            f"/api/v1/manual4/projects/{m4_1}/materials/{reqs1[('character', '林晚')]['id']}/select-or-generate",
            headers=LOCAL_ACTION_HEADERS,
            json={"mode": "generate", "prompt": "no image models"},
        )
        assert no_gen.status_code in {409, 503}

        # 2. Verify M4 project is rejected with 409 on director7 asset-center endpoints
        for method, path, kwargs in [
            ("get", f"/api/v1/asset-center/visual-facts?project_id={m4_1}", {}),
            (
                "post",
                "/api/v1/asset-center/image-jobs",
                {
                    "headers": LOCAL_ACTION_HEADERS,
                    "json": {
                        "project_id": m4_1,
                        "mode": "T2I",
                        "category": "character",
                        "title": "bad",
                        "prompt": "x",
                        "idempotency_key": "m4-blocked-img-job",
                    },
                },
            ),
            ("get", f"/api/v1/asset-center/assignments?project_id={m4_1}", {}),
            (
                "post",
                "/api/v1/asset-center/assignments",
                {
                    "headers": LOCAL_ACTION_HEADERS,
                    "json": {
                        "project_id": m4_1,
                        "library_version_id": "v1",
                        "role": "character",
                        "idempotency_key": "m4-blocked-assign-01",
                        "confirmed": True,
                    },
                },
            ),
            (
                "post",
                "/api/v1/asset-center/adoptions/preview",
                {"headers": LOCAL_ACTION_HEADERS, "json": {"project_id": m4_1, "project_asset_id": "a1"}},
            ),
            (
                "post",
                "/api/v1/asset-center/adoptions",
                {
                    "headers": LOCAL_ACTION_HEADERS,
                    "json": {
                        "project_id": m4_1,
                        "project_asset_id": "a1",
                        "idempotency_key": "m4-blocked-adopt-01",
                        "confirmed": True,
                    },
                },
            ),
        ]:
            resp = getattr(client, method)(path, **kwargs)
            assert resp.status_code == 409, f"{method} {path} -> {resp.status_code} {resp.text}"
            assert resp.json()["detail"]["code"] == "PROJECT_IS_MANUAL4_NOT_DIRECTOR7"

        # 3. Upload in M4 project 1 -> creates LibraryVersion -> promote -> visible in public library
        img = _png_bytes((220, 60, 40))
        expected_sha = hashlib.sha256(img).hexdigest()
        up = client.post(
            f"/api/v1/manual4/projects/{m4_1}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "character", "entity_name": "林晚", "requirement_id": reqs1[("character", "林晚")]["id"]},
            files={"file": ("linwan.png", img, "image/png")},
        )
        assert up.status_code == 200, up.text
        asset1 = up.json()["asset"]
        lib_ver_id = asset1["library_version_id"]
        assert lib_ver_id
        prom = client.post(
            f"/api/v1/manual4/projects/{m4_1}/assets/{asset1['id']}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={"requirement_id": reqs1[("character", "林晚")]["id"]},
        )
        assert prom.status_code == 200

        # 4. Second manual4 project searches library and adopts the SAME LibraryVersion
        search = client.get(f"/api/v1/manual4/projects/{m4_2}/materials/library?kind=image&query=林晚")
        assert search.status_code == 200
        found = search.json()["items"]
        assert any(item["library_version_id"] == lib_ver_id for item in found)

        adopt = client.post(
            f"/api/v1/manual4/projects/{m4_2}/materials/library/adopt",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "version_id": lib_ver_id,
                "requirement_id": reqs2[("character", "林晚")]["id"],
                "shot_ids": [shot2],
            },
        )
        assert adopt.status_code == 200, adopt.text
        adopted_asset = adopt.json()["asset"]
        assert adopted_asset["library_version_id"] == lib_ver_id
        assert adopted_asset["sha256"] == expected_sha
        assert adopt.json()["bound_shots"] == [shot2]

        # 5. Director7 can assign/adopt the same LibraryVersion independently via 7-stage API
        d7_assign = client.post(
            "/api/v1/asset-center/assignments",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "project_id": d7_pid,
                "library_version_id": lib_ver_id,
                "role": "character",
                "idempotency_key": "d7-assign-shared-01",
                "confirmed": True,
            },
        )
        assert d7_assign.status_code == 201, d7_assign.text

        # Zero cross-line contamination in director7 DB for manual4, and Rights authorized for all 3 projects
        with factory() as p_db:
            assert p_db.scalar(select(func.count(Asset.id)).where(Asset.project_id == d7_pid)) == 1
            assert p_db.scalar(
                select(func.count(LibraryProjectLink.id)).where(LibraryProjectLink.project_id.in_([m4_1, m4_2]))
            ) == 0
            assert p_db.scalar(select(func.count(Asset.id)).where(Asset.project_id.in_([m4_1, m4_2]))) == 0
            assert p_db.scalar(select(func.count(ShotAssetBinding.id))) == 0
            lv = p_db.get(LibraryVersion, lib_ver_id)
            assert lv is not None
            rights = p_db.get(LibraryRights, lv.rights_id)
            assert rights is not None
            assert {m4_1, m4_2}.issubset(set(rights.allowed_project_ids or []))
            assert (
                p_db.scalar(
                    select(func.count(LibraryProjectLink.id)).where(
                        LibraryProjectLink.project_id == d7_pid,
                        LibraryProjectLink.library_version_id == lib_ver_id,
                    )
                )
                == 1
            )


def test_p2_a07_lossless_legacy_m4_backfill_and_mock_promotion_refusal(tmp_path: Path):
    app, _settings, factory = _setup_app(tmp_path, with_image_recipe=False)
    m4_factory = app.state.m4_session_factory
    m4_root = app.state.m4_root

    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "A07历史无损回填"},
        ).json()["id"]

        real_bytes = _png_bytes((90, 90, 210))
        mock_bytes = _png_bytes((150, 150, 150))
        real_sha = hashlib.sha256(real_bytes).hexdigest()
        mock_sha = hashlib.sha256(mock_bytes).hexdigest()

        asset_dir = m4_root / "projects" / pid / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "legacy_real.png").write_bytes(real_bytes)
        (asset_dir / "legacy_mock.png").write_bytes(mock_bytes)

        with m4_factory() as m4_db:
            real_row = M4AssetVersion(
                project_id=pid,
                family="character",
                entity_name="历史角色",
                variant_name="",
                source_kind="upload",
                filename="legacy_real.png",
                content_type="image/png",
                sha256=real_sha,
                byte_size=len(real_bytes),
                storage_path=f"projects/{pid}/assets/legacy_real.png",
                width=64,
                height=64,
                status="promoted",
                is_mock=False,
                provenance={"legacy": True},
            )
            mock_row = M4AssetVersion(
                project_id=pid,
                family="scene",
                entity_name="历史Mock场景",
                variant_name="",
                source_kind="mock_t2i",
                filename="legacy_mock.png",
                content_type="image/png",
                sha256=mock_sha,
                byte_size=len(mock_bytes),
                storage_path=f"projects/{pid}/assets/legacy_mock.png",
                width=64,
                height=64,
                status="promoted",
                is_mock=True,
                provenance={"legacy_mock": True},
            )
            m4_db.add_all([real_row, mock_row])
            m4_db.commit()
            real_id = real_row.id
            mock_id = mock_row.id

        # Run backfill twice for idempotency
        bf1 = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/backfill-library",
            headers=LOCAL_ACTION_HEADERS,
        )
        assert bf1.status_code == 200, bf1.text
        assert bf1.json()["migrated"] == 2

        bf2 = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/backfill-library",
            headers=LOCAL_ACTION_HEADERS,
        )
        assert bf2.status_code == 200, bf2.text
        assert bf2.json()["migrated"] == 2

        with m4_factory() as m4_db, factory() as p_db:
            r_m4 = m4_db.get(M4AssetVersion, real_id)
            m_m4 = m4_db.get(M4AssetVersion, mock_id)
            assert r_m4 is not None and m_m4 is not None
            # Original SHA & path strictly untouched
            assert r_m4.sha256 == real_sha
            assert m_m4.sha256 == mock_sha
            assert r_m4.storage_path == f"projects/{pid}/assets/legacy_real.png"
            assert m_m4.storage_path == f"projects/{pid}/assets/legacy_mock.png"
            assert r_m4.library_version_id and m_m4.library_version_id

            r_lv = p_db.get(LibraryVersion, r_m4.library_version_id)
            m_lv = p_db.get(LibraryVersion, m_m4.library_version_id)
            assert r_lv is not None and r_lv.status == "PROMOTED" and r_lv.content_sha256 == real_sha
            assert m_lv is not None and m_lv.status == "CANDIDATE" and m_lv.is_mock is True
            m_rights = p_db.get(LibraryRights, m_lv.rights_id)
            assert m_rights is not None and m_rights.status == "DECLARED"
            # No duplicate LibraryVersion rows created on 2nd backfill
            assert (
                p_db.scalar(
                    select(func.count(LibraryVersion.id)).where(LibraryVersion.content_sha256 == real_sha)
                )
                == 1
            )
            assert ac_service.version_bytes(app.state.settings, r_lv) == real_bytes

        # Attempting to promote the mock asset through M4 is refused cleanly
        prom_mock = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{mock_id}/promote",
            headers=LOCAL_ACTION_HEADERS,
        )
        assert prom_mock.status_code == 409
        assert prom_mock.json()["detail"]["code"] == "LIBRARY_MOCK_PROMOTION_FORBIDDEN"
        assert m4_db.scalar(select(func.count(M4ShotAssetBinding.id))) == 0

"""P3 acceptance tests: Manual4 materials upload, generate/reuse, promote, decline & Ref2VA bindings."""

from __future__ import annotations

import sqlite3
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _minimal_png(width: int = 8, height: int = 8) -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_scanlines = b"".join(b"\x00" + (b"\x10\x80\xff" * width) for _ in range(height))
    idat = zlib.compress(raw_scanlines)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _setup_m4_client(tmp_path):
    db_path = tmp_path / "shine.db"
    database_url = f"sqlite:///{db_path.as_posix()}"
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    settings = Settings(data_dir=tmp_path, database_url=database_url, node_pack_gate="diagnose")
    app = create_app(settings=settings, database_url=database_url, asset_root=tmp_path)
    app.state.engine.dispose()
    app.state.engine = engine
    app.state.session_factory = factory
    return app, tmp_path


def _confirm_sample_storyboard(client: TestClient, title: str = "素材阶段测试") -> tuple[str, dict]:
    pid = client.post(
        "/api/v1/platform/manual4-projects",
        headers=LOCAL_ACTION_HEADERS,
        json={"title": title, "max_shot_seconds": 15.0},
    ).json()["id"]
    raw_text = (
        "第1集：测试集（目标总时长：12s）\n"
        "第1镜（6s）：雨夜窄巷中林晚撑伞回头。\n"
        "人物：林晚\n"
        "场景：雨夜窄巷\n"
        "道具：油纸伞\n"
        "第2镜（6s）：林晚独自走入窄巷深处。\n"
        "人物：林晚\n"
        "场景：雨夜窄巷\n"
    )
    client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=LOCAL_ACTION_HEADERS,
        data={"text": raw_text, "filename": "plan.txt"},
    )
    conf = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=LOCAL_ACTION_HEADERS,
        json={"acknowledge_duration_mismatch": False},
    )
    assert conf.status_code == 200
    state = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    return pid, state


def test_p3_upload_generate_reuse_promote_and_zero_seven_stage_writes(tmp_path):
    app, data_dir = _setup_m4_client(tmp_path)
    png_bytes = _minimal_png(16, 16)
    app.state.m4_image_provider = lambda **_kwargs: {
        "bytes": png_bytes,
        "filename": "qwen_cand.png",
        "is_mock": True,
        "provider": "offline-test-qwen",
    }

    with TestClient(app) as client:
        pid, mat_state = _confirm_sample_storyboard(client)
        reqs = {(r["family"], r["entity_name"]): r for r in mat_state["requirements"]}
        assert ("character", "林晚") in reqs
        assert ("scene", "雨夜窄巷") in reqs
        assert ("prop", "油纸伞") in reqs

        # 1. Invalid header upload rejected with 415
        bad_up = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "prop", "entity_name": "油纸伞", "requirement_id": reqs[("prop", "油纸伞")]["id"]},
            files={"file": ("bad.png", b"NOT_A_PNG", "image/png")},
        )
        assert bad_up.status_code == 415

        # 2. Valid upload creates candidate (NOT auto-promoted)
        up = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "character", "entity_name": "林晚", "requirement_id": reqs[("character", "林晚")]["id"]},
            files={"file": ("linwan.png", png_bytes, "image/png")},
        )
        assert up.status_code == 200
        cand = up.json()["asset"]
        assert cand["status"] == "candidate"
        assert cand["width"] == 16 and cand["height"] == 16
        assert cand["is_mock"] is False

        # Candidate cannot be bound directly to a shot before promote
        shot1_id = mat_state["shots"][0]["id"]
        premature = client.put(
            f"/api/v1/manual4/projects/{pid}/shots/{shot1_id}/bindings",
            headers=LOCAL_ACTION_HEADERS,
            json={"bindings": [{"asset_version_id": cand["id"], "use_as": "image", "role_label": "林晚"}]},
        )
        assert premature.status_code == 409
        assert premature.json()["detail"]["code"] == "M4_BINDING_REQUIRES_PROMOTED_ASSET"

        # 3. Promote character candidate -> binds requirement & both shots
        prom = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{cand['id']}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={},
        )
        assert prom.status_code == 200
        assert prom.json()["asset"]["status"] == "promoted"
        assert len(prom.json()["bound_shots"]) == 2
        media_resp = client.get(prom.json()["asset"]["media_url"])
        assert media_resp.status_code == 200 and media_resp.content == png_bytes

        # 4. Generate for scene via m4_image_provider -> candidate with is_mock=True -> promote
        gen_scene = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/{reqs[('scene', '雨夜窄巷')]['id']}/select-or-generate",
            headers=LOCAL_ACTION_HEADERS,
            json={"mode": "generate", "prompt": "Rainy dark alley"},
        )
        assert gen_scene.status_code == 200
        scene_cand = gen_scene.json()["asset"]
        assert scene_cand["status"] == "candidate"
        assert scene_cand["is_mock"] is True
        prom_scene = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{scene_cand['id']}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={},
        )
        assert prom_scene.status_code == 200

        # 5. Standalone upload + promote + reuse for prop
        stand_up = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "prop", "entity_name": "油纸伞"},
            files={"file": ("umbrella.png", png_bytes, "image/png")},
        )
        umbrella_id = stand_up.json()["asset"]["id"]
        client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{umbrella_id}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={},
        )
        reused = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/{reqs[('prop', '油纸伞')]['id']}/select-or-generate",
            headers=LOCAL_ACTION_HEADERS,
            json={"mode": "reuse", "asset_version_id": umbrella_id},
        )
        assert reused.status_code == 200
        assert reused.json()["state"]["readiness"]["stages"][1]["done"] is True

    # Zero writes to 7-stage tables in shine.db
    with sqlite3.connect(data_dir / "shine.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM candidate_assets").fetchone()[0] == 0
    with sqlite3.connect(data_dir / "manual4.sqlite") as mconn:
        assert mconn.execute("SELECT COUNT(*) FROM m4_asset_versions WHERE status='promoted'").fetchone()[0] == 3


def test_p3_decline_preserved_across_storyboard_reconfirm_and_slot_limit_enforced(tmp_path):
    app, _data_dir = _setup_m4_client(tmp_path)
    png_bytes = _minimal_png(16, 16)

    with TestClient(app) as client:
        pid, mat_state = _confirm_sample_storyboard(client, "放弃与槽位上限测试")
        reqs = {(r["family"], r["entity_name"]): r for r in mat_state["requirements"]}

        # Decline prop requirement explicitly
        dec = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/{reqs[('prop', '油纸伞')]['id']}/decline",
            headers=LOCAL_ACTION_HEADERS,
            json={"reason": "不需要独立道具参考图"},
        )
        assert dec.status_code == 200
        assert dec.json()["user_declined"] is True
        declined_req = next(
            r for r in dec.json()["state"]["requirements"] if r["entity_name"] == "油纸伞"
        )
        assert declined_req["user_declined"] is True
        assert declined_req["status"] == "declined"

        # Upload & promote only character -> stage 2 still blocked because scene isn't handled
        up_char = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "character", "entity_name": "林晚", "requirement_id": reqs[("character", "林晚")]["id"]},
            files={"file": ("linwan.png", png_bytes, "image/png")},
        ).json()["asset"]
        client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{up_char['id']}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={},
        )

        # Decline scene too -> both shots still have "林晚" bound, so Ref2VA ready on both shots & Stage 2 passes!
        client.post(
            f"/api/v1/manual4/projects/{pid}/materials/{reqs[('scene', '雨夜窄巷')]['id']}/decline",
            headers=LOCAL_ACTION_HEADERS,
            json={"reason": "按人物单图驱动"},
        )
        after = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
        assert after["readiness"]["stages"][1]["done"] is True

        # Re-confirm storyboard revision: declined state MUST survive and NOT be wiped
        draft = client.get(f"/api/v1/manual4/projects/{pid}/storyboard/draft").json()["draft"]
        client.put(
            f"/api/v1/manual4/projects/{pid}/storyboard/draft",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": draft["revision"], "payload": draft["payload"]},
        )
        client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"acknowledge_duration_mismatch": False},
        )
        after_reconfirm = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
        for r in after_reconfirm["requirements"]:
            if r["entity_name"] in {"油纸伞", "雨夜窄巷"}:
                assert r["user_declined"] is True
                assert r["status"] == "declined"

        # Unbind shot1 only -> only shot1 blocked by M4_SHOT_REQUIRES_REF2VA_REFERENCE, shot2 stays ref2va_ready
        shot1_id = after_reconfirm["shots"][0]["id"]
        unbind = client.put(
            f"/api/v1/manual4/projects/{pid}/shots/{shot1_id}/bindings",
            headers=LOCAL_ACTION_HEADERS,
            json={"bindings": []},
        )
        assert unbind.status_code == 200
        blockers = unbind.json()["state"]["readiness"]["stages"][1]["blockers"]
        ref_blockers = [b for b in blockers if b["code"] == "M4_SHOT_REQUIRES_REF2VA_REFERENCE"]
        assert len(ref_blockers) == 1 and ref_blockers[0]["shot_id"] == shot1_id
        assert unbind.json()["state"]["shots"][1]["ref2va_ready"] is True

        # Enforce H3 MAX_IMAGE_SLOTS = 6
        promoted_ids = []
        for idx in range(7):
            p = client.post(
                f"/api/v1/manual4/projects/{pid}/assets/upload",
                headers=LOCAL_ACTION_HEADERS,
                data={"family": "prop", "entity_name": f"extra-{idx}"},
                files={"file": (f"e{idx}.png", png_bytes, "image/png")},
            ).json()["asset"]
            client.post(
                f"/api/v1/manual4/projects/{pid}/assets/{p['id']}/promote",
                headers=LOCAL_ACTION_HEADERS,
                json={},
            )
            promoted_ids.append(p["id"])
        too_many = client.put(
            f"/api/v1/manual4/projects/{pid}/shots/{shot1_id}/bindings",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "bindings": [
                    {"asset_version_id": aid, "use_as": "image", "role_label": f"r{i}"}
                    for i, aid in enumerate(promoted_ids)
                ]
            },
        )
        assert too_many.status_code == 422
        assert too_many.json()["detail"]["code"] == "M4_REF2VA_SLOT_LIMIT_EXCEEDED"


def test_p3_autonomous_stage2_allows_unprepared_requirements_when_every_shot_has_ref2va(tmp_path):
    app, _ = _setup_m4_client(tmp_path)
    png_bytes = _minimal_png(16, 16)

    with TestClient(app) as client:
        pid, mat_state = _confirm_sample_storyboard(client, "自主制片按镜放行测试")
        reqs = {(r["family"], r["entity_name"]): r for r in mat_state["requirements"]}

        # Only prepare & promote character 林晚 (which covers both shot 1 and shot 2).
        # Leave scene 雨夜窄巷 and prop 油纸伞 completely unprepared and NOT declined.
        up_char = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "character", "entity_name": "林晚", "requirement_id": reqs[("character", "林晚")]["id"]},
            files={"file": ("linwan.png", png_bytes, "image/png")},
        ).json()["asset"]
        prom = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{up_char['id']}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={},
        )
        assert prom.status_code == 200
        state = prom.json()["state"]
        stage2 = state["readiness"]["stages"][1]
        assert stage2["done"] is True
        assert stage2["blockers"] == []
        assert state["readiness"]["next_stage"] == "production"
        assert stage2["facts"]["shots_ready"] == 2
        assert stage2["facts"]["shots_total"] == 2

        # Unbind shot 1 -> Stage 2 MUST block on shot 1 missing Ref2VA (not on missing requirements)
        shot1_id = state["shots"][0]["id"]
        unbind = client.put(
            f"/api/v1/manual4/projects/{pid}/shots/{shot1_id}/bindings",
            headers=LOCAL_ACTION_HEADERS,
            json={"bindings": []},
        ).json()["state"]
        assert unbind["readiness"]["stages"][1]["done"] is False
        codes = [b["code"] for b in unbind["readiness"]["stages"][1]["blockers"]]
        assert codes == ["M4_SHOT_REQUIRES_REF2VA_REFERENCE"]


def test_p3_batch_operations_generate_promote_and_decline(tmp_path):
    app, _ = _setup_m4_client(tmp_path)
    png_bytes = _minimal_png(16, 16)
    app.state.m4_image_provider = lambda **_kwargs: {
        "bytes": png_bytes,
        "filename": "batch_cand.png",
        "is_mock": True,
        "provider": "offline-test-qwen",
    }

    with TestClient(app) as client:
        pid, mat_state = _confirm_sample_storyboard(client, "批量操作测试")
        req_ids = [r["id"] for r in mat_state["requirements"]]
        char_req = next(r for r in mat_state["requirements"] if r["entity_name"] == "林晚")
        scene_req = next(r for r in mat_state["requirements"] if r["entity_name"] == "雨夜窄巷")
        prop_req = next(r for r in mat_state["requirements"] if r["entity_name"] == "油纸伞")

        # 1. Batch generate for character + scene
        gen_resp = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/batch",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "action": "generate",
                "requirement_ids": [char_req["id"], scene_req["id"]],
                "prompts_by_req": {char_req["id"]: "Lin Wan portrait"},
            },
        )
        assert gen_resp.status_code == 200
        assert set(gen_resp.json()["applied_ids"]) == {char_req["id"], scene_req["id"]}
        assert gen_resp.json()["errors"] == []

        # 2. Batch promote latest candidates for all 3 (prop has no candidate -> skipped)
        prom_resp = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/batch",
            headers=LOCAL_ACTION_HEADERS,
            json={"action": "promote_latest", "requirement_ids": req_ids},
        )
        assert prom_resp.status_code == 200
        assert set(prom_resp.json()["applied_ids"]) == {char_req["id"], scene_req["id"]}
        assert len(prom_resp.json()["skipped"]) == 1
        assert prom_resp.json()["skipped"][0]["requirement_id"] == prop_req["id"]
        assert prom_resp.json()["state"]["readiness"]["stages"][1]["done"] is True

        # 3. Batch decline prop
        dec_resp = client.post(
            f"/api/v1/manual4/projects/{pid}/materials/batch",
            headers=LOCAL_ACTION_HEADERS,
            json={"action": "decline", "requirement_ids": [prop_req["id"]]},
        )
        assert dec_resp.status_code == 200
        assert dec_resp.json()["applied_ids"] == [prop_req["id"]]
        prop_after = next(r for r in dec_resp.json()["state"]["requirements"] if r["id"] == prop_req["id"])
        assert prop_after["user_declined"] is True

"""P4 verification tests: storyboard invalidation, chunked LLM extraction, Ref2VA gates, and delivery freshness."""

from __future__ import annotations

import sqlite3
import struct
import zlib

import pytest
from fastapi.testclient import TestClient

from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app
from shine.manual4.materials.probe import make_mp4_fixture
from shine.manual4.status.readiness import evaluate_shot_ref2va_bindings
from shine.manual4.storage.files import store_m4_bytes
from shine.manual4.storage.models import (
    M4AssetVersion,
    M4Delivery,
    M4Job,
    M4ProductionRun,
    M4ShotAssetBinding,
)
from shine.manual4.worker import execute_next_m4_run
from shine.models import ProviderProfile

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _minimal_png(width: int = 16, height: int = 16) -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + (b"\x18\x55\xb0" * width) for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def _minimal_wav() -> bytes:
    sr = 8000
    data = b"\x00\x00" * 800
    riff_size = 36 + len(data)
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


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


def _prepare_frozen_project(client: TestClient) -> tuple[str, dict, list[dict]]:
    pid = client.post(
        "/api/v1/platform/manual4-projects",
        headers=LOCAL_ACTION_HEADERS,
        json={"title": "P4 缺陷修复与就绪测试", "max_shot_seconds": 15.0},
    ).json()["id"]
    raw_text = (
        "第1集：雨巷（目标总时长：10s）\n"
        "第1镜（5s）：林晚撑伞站在青石巷口。\n"
        "人物：林晚\n"
        "第2镜（5s）：路灯照亮她衣角的水渍。\n"
        "人物：林晚\n"
    )
    imp = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=LOCAL_ACTION_HEADERS,
        data={"text": raw_text, "filename": "plan.txt"},
    )
    assert imp.status_code == 201
    conf = client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=LOCAL_ACTION_HEADERS,
        json={"expected_revision": imp.json()["draft"]["revision"], "acknowledge_duration_mismatch": False},
    )
    assert conf.status_code == 200
    mat = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    req_id = mat["requirements"][0]["id"]
    up = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/upload",
        headers=LOCAL_ACTION_HEADERS,
        data={"family": "character", "entity_name": "林晚", "requirement_id": req_id},
        files={"file": ("linwan.png", _minimal_png(), "image/png")},
    ).json()["asset"]
    prom = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/{up['id']}/promote",
        headers=LOCAL_ACTION_HEADERS,
        json={},
    ).json()
    return pid, imp.json()["draft"], prom["state"]["shots"]


def test_p4_shot_content_and_render_options_invalidate_frozen_prompts(tmp_path):
    app, data_dir = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid, draft, shots = _prepare_frozen_project(client)
        s1, s2 = shots[0], shots[1]

        with sqlite3.connect(data_dir / "manual4.sqlite") as conn:
            sha_before = conn.execute(
                "SELECT content_sha256, revision FROM m4_shots WHERE id = ?",
                (s1["id"],),
            ).fetchone()
            assert sha_before[0]
            assert sha_before[1] == 1

        # Freeze both prompts
        for idx, sh in enumerate((s1, s2), start=1):
            fr = client.post(
                f"/api/v1/manual4/projects/{pid}/shots/{sh['id']}/prompt/freeze",
                headers=LOCAL_ACTION_HEADERS,
                json={"prompt_text": f"detailed_description:\nShot {idx} prompt text"},
            )
            assert fr.status_code == 200

        # 1) Updating render options on shot 1 bumps revision & invalidates frozen prompt while keeping prompt_text
        opt = client.put(
            f"/api/v1/manual4/projects/{pid}/shots/{s1['id']}/render-options",
            headers=LOCAL_ACTION_HEADERS,
            json={"aspect_ratio": "9:16", "continuity_mode": "last_frame"},
        )
        assert opt.status_code == 200
        assert opt.json()["shot_revision"] == 2
        prod1 = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
        p1 = next(sh["active_prompt"] for sh in prod1["episodes"][0]["shots"] if sh["id"] == s1["id"])
        assert p1["status"] == "INVALIDATED"
        assert p1["invalidated_reason"] == "M4_SHOT_OPTIONS_CHANGED"
        assert "Shot 1 prompt text" in p1["prompt_text"]

        # Re-freeze shot 1
        fr_re = client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{s1['id']}/prompt/freeze",
            headers=LOCAL_ACTION_HEADERS,
            json={"prompt_text": p1["prompt_text"]},
        )
        assert fr_re.status_code == 200

        # 2) Changing only shot 1 summary text (preserving updated options) bumps shot revision & invalidates prompt
        cur_draft = client.get(f"/api/v1/manual4/projects/{pid}/storyboard/draft").json()["draft"]
        payload = cur_draft["payload"]
        payload["episodes"][0]["shots"][0]["summary"] = "林晚推开木门走进昏暗走廊。"
        payload["episodes"][0]["shots"][0]["aspect_ratio"] = "9:16"
        payload["episodes"][0]["shots"][0]["continuity_mode"] = "last_frame"
        saved = client.put(
            f"/api/v1/manual4/projects/{pid}/storyboard/draft",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": cur_draft["revision"], "payload": payload},
        ).json()["draft"]
        conf2 = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": saved["revision"], "acknowledge_duration_mismatch": False},
        )
        assert conf2.status_code == 200

        prod2 = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
        s1_after = prod2["episodes"][0]["shots"][0]
        p1_after = s1_after["active_prompt"]
        p2_after = prod2["episodes"][0]["shots"][1]["active_prompt"]
        with sqlite3.connect(data_dir / "manual4.sqlite") as conn:
            sha_after = conn.execute(
                "SELECT content_sha256, revision FROM m4_shots WHERE id = ?",
                (s1_after["id"],),
            ).fetchone()
            assert sha_after[0] != sha_before[0]
            assert sha_after[1] >= 1

        assert p1_after["status"] == "DRAFT"
        assert p1_after["invalidated_reason"] == "M4_STORYBOARD_SHOT_CHANGED"
        assert "Shot 1 prompt text" in p1_after["prompt_text"]
        assert p2_after["status"] == "FROZEN"


def test_p4_chunked_llm_extraction_and_strict_loopback_guard(tmp_path, monkeypatch):
    import httpx

    app, _ = _setup_m4_client(tmp_path)
    with app.state.session_factory.begin() as sdb:
        sdb.add(
            ProviderProfile(
                id="llm-spoof",
                name="Spoofed Loopback Host",
                kind="llm",
                model="qwen-test",
                base_url="http://127.0.0.1.evil.com:11434/v1",
            )
        )
        sdb.add(
            ProviderProfile(
                id="llm-local",
                name="Local Ollama",
                kind="llm",
                model="qwen2.5:14b",
                base_url="http://127.0.0.1:11434/v1",
            )
        )

    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "LLM 分段抽取与 SSRF 护栏"},
        ).json()["id"]

        # Host starting with 127.0.0.1 (127.0.0.1.evil.com) MUST still require remote consent
        blocked = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={
                "text": "一段完全没有第几集第几镜格式的长自由散文" * 10,
                "use_llm": "true",
                "llm_profile_id": "llm-spoof",
                "remote_consent": "false",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "M4_REMOTE_LLM_CONSENT_REQUIRED"

        # Build >12,000-character unstructured text (no rule-based 第N镜 markers)
        prose_para = "霓虹灯在暴雨中的玻璃幕墙上流淌，顾言紧握着怀里的金属钥匙站在十字路口。"
        long_prose = "\n\n".join(f"段落{i}：{prose_para * 8}" for i in range(1, 55))
        assert len(long_prose) > 12000

        seen_chunks: list[str] = []

        def fake_llm_chunk(chunk_text: str):
            chunk_index = len(seen_chunks)
            seen_chunks.append(chunk_text)
            return {
                "episodes": [
                    {
                        "episode_no": 1,
                        "episode_code": "ep01",
                        "title": "暴雨夜",
                        "shots": [
                            {
                                "episode_code": "ep01",
                                "shot_no": chunk_index + 1,
                                "shot_code": f"ep01-s{chunk_index + 1:02d}",
                                "target_seconds": 5.0,
                                "summary": f"分块{chunk_index + 1}抽取画面（长度 {len(chunk_text)}）",
                                "requested_assets": [{"family": "character", "name": "顾言"}],
                                "source_span": {"start_char": 5, "end_char": min(60, len(chunk_text))},
                            }
                        ],
                    }
                ],
                "ambiguities": [
                    {
                        "code": "M4_CHUNK_NOTE",
                        "message": f"chunk-{chunk_index} ambiguity",
                        "shot_code": f"ep01-s{chunk_index + 1:02d}",
                    }
                ],
            }

        app.state.m4_llm_responder = fake_llm_chunk
        ok = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={
                "text": long_prose,
                "filename": "prose.txt",
                "use_llm": "true",
                "llm_profile_id": "llm-local",
            },
        )
        assert ok.status_code == 201
        assert len(seen_chunks) >= 2
        draft = ok.json()["draft"]
        assert draft["payload"]["extractor"] == "llm_assisted"
        shots = draft["payload"]["episodes"][0]["shots"]
        assert len(shots) == len(seen_chunks)
        assert shots[-1]["source_span"]["start_offset"] > 1000
        assert len(draft["payload"]["ambiguities"]) == len(seen_chunks)

        # LLM runtime failure -> 502 M4_LLM_EXTRACT_FAILED (not silent empty success)
        app.state.m4_llm_responder = None
        monkeypatch.setattr(
            httpx,
            "post",
            lambda *_a, **_kw: (_ for _ in ()).throw(httpx.ConnectError("upstream timeout")),
        )
        err = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={
                "text": "纯文本无结构" * 20,
                "use_llm": "true",
                "llm_profile_id": "llm-local",
            },
        )
        assert err.status_code == 502
        assert err.json()["detail"]["code"] == "M4_LLM_EXTRACT_FAILED"


def test_p4_stage2_ref2va_refuses_audio_only_and_enforces_slot_limits(tmp_path):
    app, _ = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "Stage2 Ref2VA 门禁测试"},
        ).json()["id"]
        raw_text = "第1集：单镜\n第1镜（5s）：人物独白。\n"
        imp = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={"text": raw_text},
        ).json()["draft"]
        client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": imp["revision"]},
        )

        # Upload & promote audio-only reference, bind to shot 1 without any image
        wav = client.post(
            f"/api/v1/manual4/projects/{pid}/assets/upload",
            headers=LOCAL_ACTION_HEADERS,
            data={"family": "voice", "entity_name": "独白音色"},
            files={"file": ("voice.wav", _minimal_wav(), "audio/wav")},
        ).json()["asset"]
        client.post(
            f"/api/v1/manual4/projects/{pid}/assets/{wav['id']}/promote",
            headers=LOCAL_ACTION_HEADERS,
            json={},
        )
        mat0 = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
        shot_id = mat0["shots"][0]["id"]
        bound = client.put(
            f"/api/v1/manual4/projects/{pid}/shots/{shot_id}/bindings",
            headers=LOCAL_ACTION_HEADERS,
            json={"bindings": [{"asset_version_id": wav["id"], "use_as": "audio", "role_label": "音色"}]},
        )
        assert bound.status_code == 200
        mat_state = bound.json()["state"]
        assert mat_state["slot_limits"] == {"image": 6, "audio": 2, "video": 2}
        assert mat_state["shots"][0]["ref2va_ready"] is False
        assert mat_state["shots"][0]["ref2va_blocker"]["code"] == "PROJECT_REQUIRES_REF2VA"
        stage2 = next(s for s in mat_state["readiness"]["stages"] if s["key"] == "materials")
        assert stage2["done"] is False
        assert any(b["code"] == "PROJECT_REQUIRES_REF2VA" for b in stage2["blockers"])

        # Direct evaluation rejects > 9 image slots cleanly
        img_asset = M4AssetVersion(
            id="img-a",
            project_id=pid,
            family="character",
            entity_name="img",
            filename="img.png",
            content_type="image/png",
            storage_path="x.png",
            sha256="a" * 64,
            byte_size=12,
            status="promoted",
        )
        many_links = [
            M4ShotAssetBinding(project_id=pid, shot_id=shot_id, asset_version_id="img-a", use_as="image", ordinal=i)
            for i in range(10)
        ]
        ok, err_code, _ = evaluate_shot_ref2va_bindings(many_links, {"img-a": img_asset})
        assert ok is False
        assert err_code == "M4_SHOT_REF2VA_SLOT_LIMIT_EXCEEDED"


def test_p4_delivery_freshness_prefers_real_and_detects_broken_sha(tmp_path):
    app, _ = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid, _, shots = _prepare_frozen_project(client)
        for idx, sh in enumerate(shots, start=1):
            client.post(
                f"/api/v1/manual4/projects/{pid}/shots/{sh['id']}/prompt/freeze",
                headers=LOCAL_ACTION_HEADERS,
                json={"prompt_text": f"detailed_description:\nShot {idx} prompt"},
            )

        # Create a mock passed run first
        mock_run = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "mock", "resolution_scale": 0.5},
        ).json()["run"]
        execute_next_m4_run(
            app.state.session_factory,
            app.state.m4_session_factory,
            app.state.settings,
            app.state.m4_root,
            worker_id="p4-mock-w",
            video_stub=lambda *, shot_code, duration_s, width, height, **_kw: {
                "bytes": make_mp4_fixture(duration_s=duration_s, width=width, height=height, fps=24),
                "filename": f"{shot_code}_mock.mp4",
                "is_mock": True,
            },
        )
        assert mock_run["id"]

        # Create a matching real (provider="comfyui") passed job for shot 1 with real stub artifact
        real_run = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "comfyui", "resolution_scale": 0.5},
        ).json()["run"]
        app.state.m4_delivery_assembler_stub = lambda *, episode_code, **_kw: {
            "bytes": make_mp4_fixture(duration_s=10.0, width=672, height=384, fps=24),
            "is_mock": False,
        }
        with app.state.m4_session_factory.begin() as mdb:
            run_row = mdb.get(M4ProductionRun, real_run["id"])
            if run_row is not None:
                run_row.status = "SUCCEEDED"
            for jdict in real_run["jobs"]:
                job = mdb.get(M4Job, jdict["id"])
                rel, sha, out_path = store_m4_bytes(
                    app.state.m4_root,
                    pid,
                    "renders",
                    f"{job.id}_real.mp4",
                    make_mp4_fixture(duration_s=5.0, width=672, height=384, fps=24),
                )
                job.status = "PASSED"
                job.output_storage_path = rel
                job.output_sha256 = sha
                job.output_bytes = out_path.stat().st_size
                job.is_mock = False

        dstate = client.get(f"/api/v1/manual4/projects/{pid}/delivery/state").json()
        ep_info = dstate["episodes"][0]
        ep_id = ep_info["id"]
        assert ep_info["ready_to_assemble"] is True
        # Shot 1 candidate list must sort real comfyui job before mock job
        s1_cands = next(sc["passed_jobs"] for sc in ep_info["shots"] if sc["shot_id"] == shots[0]["id"])
        assert s1_cands[0]["provider"] == "comfyui"

        asm = client.post(
            f"/api/v1/manual4/projects/{pid}/episodes/{ep_id}/deliveries/assemble",
            headers=LOCAL_ACTION_HEADERS,
            json={"include_subtitles": False},
        )
        assert asm.status_code == 201
        d1 = asm.json()["delivery"]
        # Record acceptance, then verify Stage 4 distinct confirmed episode count & corrupt file SHA check
        acc = client.post(
            f"/api/v1/manual4/projects/{pid}/deliveries/{d1['id']}/accept",
            headers=LOCAL_ACTION_HEADERS,
            json={"decision": "accepted", "watched": True, "evidence": "verified", "expected_sha256": d1["sha256"]},
        )
        assert acc.status_code == 200
        dstate2 = acc.json()["state"]
        assert dstate2["deliveries"][0]["acceptance"] is not None
        stage4 = next(s for s in dstate2["readiness"]["stages"] if s["key"] == "delivery")
        assert stage4["done"] is True
        assert stage4["facts"]["accepted_deliveries"] == 1

        # Corrupt assembled file on disk -> media endpoint MUST refuse with 409 M4_DELIVERY_SHA_MISMATCH
        with app.state.m4_session_factory() as mdb:
            rel_path = mdb.get(M4Delivery, d1["id"]).storage_path
        disk_path = app.state.m4_root / rel_path
        disk_path.write_bytes(b"corrupted-video-bytes")
        bad_media = client.get(f"/api/v1/manual4/projects/{pid}/deliveries/{d1['id']}/media")
        assert bad_media.status_code == 409
        assert bad_media.json()["detail"]["code"] == "M4_DELIVERY_SHA_MISMATCH"

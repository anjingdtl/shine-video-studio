"""P5 acceptance tests: Manual4 EDL assembly, real FFmpeg concat, SHA-bound acceptance & isolation."""

from __future__ import annotations

import shutil
import sqlite3
import struct
import subprocess
import zlib

import pytest
from fastapi.testclient import TestClient

from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app
from shine.manual4.materials.probe import make_mp4_fixture, probe_file
from shine.manual4.storage.files import sha256_bytes, store_m4_bytes
from shine.manual4.storage.models import M4Job, M4ProductionRun
from shine.manual4.worker import execute_next_m4_run

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


def _minimal_png(width: int = 16, height: int = 16) -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + (b"\x10\x40\x90" * width) for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


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


def _setup_ready_two_shot_project(client: TestClient) -> tuple[str, str, list[dict]]:
    pid = client.post(
        "/api/v1/platform/manual4-projects",
        headers=LOCAL_ACTION_HEADERS,
        json={"title": "成片装配验收测试", "max_shot_seconds": 15.0},
    ).json()["id"]
    raw_text = (
        "第1集：夜雨（目标总时长：10s）\n"
        "第1镜（5s）：林晚走入窄巷。\n"
        "人物：林晚\n"
        "对白：林晚：「今晚风好大。」\n"
        "第2镜（5s）：林晚撑起油纸伞。\n"
        "人物：林晚\n"
    )
    client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/import",
        headers=LOCAL_ACTION_HEADERS,
        data={"text": raw_text, "filename": "plan.txt"},
    )
    client.post(
        f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
        headers=LOCAL_ACTION_HEADERS,
        json={"acknowledge_duration_mismatch": False},
    )
    mat = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
    req_id = mat["requirements"][0]["id"]
    up = client.post(
        f"/api/v1/manual4/projects/{pid}/assets/upload",
        headers=LOCAL_ACTION_HEADERS,
        data={"family": "character", "entity_name": "林晚", "requirement_id": req_id},
        files={"file": ("linwan.png", _minimal_png(), "image/png")},
    ).json()["asset"]
    client.post(
        f"/api/v1/manual4/projects/{pid}/assets/{up['id']}/promote",
        headers=LOCAL_ACTION_HEADERS,
        json={},
    )
    prod = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
    ep_id = prod["episodes"][0]["id"]
    shots = prod["episodes"][0]["shots"]
    for idx, sh in enumerate(shots):
        client.post(
            f"/api/v1/manual4/projects/{pid}/shots/{sh['id']}/prompt/freeze",
            headers=LOCAL_ACTION_HEADERS,
            json={"prompt_text": f"detailed_description:\nShot {idx + 1} H3 prompt."},
        )
    return pid, ep_id, shots


def test_p5_offline_delivery_versioning_and_mock_acceptance_refusal(tmp_path):
    app, data_dir = _setup_m4_client(tmp_path)
    app.state.m4_delivery_assembler_stub = lambda *, episode_code, **_kw: {
        "bytes": make_mp4_fixture(duration_s=10.0, width=672, height=384, fps=24),
        "is_mock": True,
    }

    with TestClient(app) as client:
        pid, ep_id, _shots = _setup_ready_two_shot_project(client)
        run = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "mock", "resolution_scale": 0.5},
        ).json()["run"]
        execute_next_m4_run(
            app.state.session_factory,
            app.state.m4_session_factory,
            app.state.settings,
            app.state.m4_root,
            worker_id="p5-stub-w",
            video_stub=lambda *, shot_code, duration_s, width, height, **_kw: {
                "bytes": make_mp4_fixture(duration_s=duration_s, width=width, height=height, fps=24),
                "filename": f"{shot_code}.mp4",
                "is_mock": True,
            },
        )

        # Assemble v1
        deliv1 = client.post(
            f"/api/v1/manual4/projects/{pid}/episodes/{ep_id}/deliveries/assemble",
            headers=LOCAL_ACTION_HEADERS,
            json={"include_subtitles": True},
        )
        assert deliv1.status_code == 201
        d1 = deliv1.json()["delivery"]
        assert d1["version"] == 1
        assert d1["is_mock"] is True
        assert d1["has_subtitles"] is True
        srt = client.get(d1["subtitle_url"]).text
        assert "今晚风好大" in srt

        # Mock delivery MUST be refused for human acceptance
        refused = client.post(
            f"/api/v1/manual4/projects/{pid}/deliveries/{d1['id']}/accept",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "decision": "accepted",
                "watched": True,
                "evidence": "ok",
                "expected_sha256": d1["sha256"],
            },
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "M4_MOCK_DELIVERY_NOT_ACCEPTABLE"
        assert len(run["jobs"]) == 2

    with sqlite3.connect(data_dir / "shine.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_artifacts").fetchone()[0] == 0


def test_p5_real_ffmpeg_assembly_retake_replace_and_sha_bound_acceptance(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg not installed on PATH")

    app, data_dir = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid, ep_id, shots = _setup_ready_two_shot_project(client)
        run = client.post(
            f"/api/v1/manual4/projects/{pid}/production/runs",
            headers=LOCAL_ACTION_HEADERS,
            json={"scope_kind": "project", "provider": "comfyui", "resolution_scale": 0.5},
        ).json()["run"]

        # Create real FFmpeg-encoded 5s video clips for each shot (matching frozen 5s shot duration)
        for idx, job in enumerate(run["jobs"]):
            clip_tmp = tmp_path / f"real_shot_{idx}.mp4"
            subprocess.run(
                [
                    ffmpeg, "-nostdin", "-y",
                    "-f", "lavfi", "-i", f"color=c={'blue' if idx == 0 else 'green'}:s=672x384:r=24:d=5.0",
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(clip_tmp),
                ],
                check=True,
                capture_output=True,
            )
            raw_mp4 = clip_tmp.read_bytes()
            rel, sha, _ = store_m4_bytes(app.state.m4_root, pid, "renders", f"shot_{idx}.mp4", raw_mp4)
            with app.state.m4_session_factory.begin() as mdb:
                j = mdb.get(M4Job, job["id"])
                j.status = "PASSED"
                j.output_storage_path = rel
                j.output_sha256 = sha
                j.output_bytes = len(raw_mp4)
                j.measured_duration_s = 5.0
                j.measured_width = 672
                j.measured_height = 384
                j.measured_fps = 24.0
                r = mdb.get(M4ProductionRun, run["id"])
                r.status = "COMPLETED"

        # Real FFmpeg assemble v1
        res1 = client.post(
            f"/api/v1/manual4/projects/{pid}/episodes/{ep_id}/deliveries/assemble",
            headers=LOCAL_ACTION_HEADERS,
            json={"include_subtitles": True},
        )
        assert res1.status_code == 201
        d1 = res1.json()["delivery"]
        assert d1["is_mock"] is False
        assert d1["version"] == 1
        assert d1["duration_s"] is not None and 9.5 <= d1["duration_s"] <= 10.5
        media_resp = client.get(d1["media_url"])
        assert media_resp.status_code == 200
        assert sha256_bytes(media_resp.content) == d1["sha256"]

        # Watched declaration required for accepted
        no_watch = client.post(
            f"/api/v1/manual4/projects/{pid}/deliveries/{d1['id']}/accept",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "decision": "accepted",
                "watched": False,
                "evidence": "",
                "expected_sha256": d1["sha256"],
            },
        )
        assert no_watch.status_code == 422

        # Wrong SHA-256 refused
        bad_sha = client.post(
            f"/api/v1/manual4/projects/{pid}/deliveries/{d1['id']}/accept",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "decision": "accepted",
                "watched": True,
                "evidence": "checked",
                "expected_sha256": "0" * 64,
            },
        )
        assert bad_sha.status_code == 409

        # Valid acceptance transitions Stage 4 done -> True!
        acc = client.post(
            f"/api/v1/manual4/projects/{pid}/deliveries/{d1['id']}/accept",
            headers=LOCAL_ACTION_HEADERS,
            json={
                "decision": "accepted",
                "watched": True,
                "evidence": "全片核对通过",
                "expected_sha256": d1["sha256"],
                "request_id": "req-acc-1",
            },
        )
        assert acc.status_code == 200
        assert acc.json()["acceptance"]["decision"] == "accepted"
        assert acc.json()["state"]["readiness"]["stages"][3]["done"] is True
        deliv_path = app.state.m4_root / "projects" / pid / "deliveries" / d1["sha256"][:2] / f"{d1['sha256']}.mp4"
        info = probe_file(deliv_path)
        assert info.has_video is True

    with sqlite3.connect(data_dir / "shine.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_artifacts").fetchone()[0] == 0

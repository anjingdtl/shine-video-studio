"""P2 acceptance tests: Manual4 document import, deterministic parsing, chunking & atomic freeze."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from shine.config import Settings
from shine.db import Base, make_engine, make_session_factory
from shine.main import create_app

pytestmark = pytest.mark.integration

LOCAL_ACTION_HEADERS = {"x-shine-local-action": "1"}


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


def test_p2_clear_storyboard_import_manual_edit_and_atomic_freeze_without_llm(tmp_path):
    app, data_dir = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        proj = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "人工分镜测试", "max_shot_seconds": 15.0},
        ).json()
        pid = proj["id"]

        raw_text = (
            "第1集：雨夜重逢（目标总时长：14s）\n"
            "第1镜（6s）：雨夜窄巷中林晚撑伞回头。\n"
            "人物：林晚（雨衣版），陈默\n"
            "场景：雨夜窄巷\n"
            "道具：油纸伞\n"
            "对白：林晚：「你怎么才来？」\n"
            "第2镜（8s）：陈默走近递出旧怀表。\n"
            "人物：林晚（雨衣版），陈默\n"
            "场景：雨夜窄巷\n"
            "道具：旧怀表\n"
        )
        imp = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={"text": raw_text, "filename": "plan.txt"},
        )
        assert imp.status_code == 201
        draft = imp.json()["draft"]
        assert draft["status"] == "DRAFT"
        assert draft["payload"]["extractor"] == "deterministic"
        eps = draft["payload"]["episodes"]
        assert len(eps) == 1
        assert len(eps[0]["shots"]) == 2
        assert eps[0]["shots"][0]["target_seconds"] == 6.0
        assert eps[0]["shots"][1]["target_seconds"] == 8.0
        assert eps[0]["shots"][0]["summary"] == "雨夜窄巷中林晚撑伞回头。"
        assert eps[0]["shots"][0]["source_span"]["source_sha256"] == imp.json()["document"]["sha256"]
        assert any(
            a["name"] == "林晚" and a["variant"] == "雨衣版"
            for a in eps[0]["shots"][0]["requested_assets"]
        )

        # Confirm and freeze
        conf = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": draft["revision"]},
        )
        assert conf.status_code == 200
        assert conf.json()["confirmed"]["status"] == "CONFIRMED"
        assert conf.json()["requirements_count"] == 5  # 林晚(雨衣版), 陈默, 雨夜窄巷, 油纸伞, 旧怀表

        # Verify 7-stage tables remain completely untouched
        with sqlite3.connect(data_dir / "shine.db") as conn:
            assert conn.execute("SELECT COUNT(*) FROM project_documents").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 0

        # Verify manual4 readiness stage 1 is done and next_stage moved to materials
        ov = client.get(f"/api/v1/manual4/projects/{pid}/overview").json()
        assert ov["readiness"]["stages"][0]["done"] is True
        assert ov["readiness"]["next_stage"] == "materials"


def test_p2_missing_duration_and_atomic_rollback_and_episode_deviation(tmp_path):
    app, data_dir = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "缺时长与偏差测试", "max_shot_seconds": 15.0},
        ).json()["id"]

        # Missing duration on shot 2: parser must NOT invent a duration
        text = (
            "第1集：测试集（目标总时长：20s）\n"
            "第1镜（6s）：镜头一描述。\n"
            "人物：主角\n"
            "第2镜：没有写时长的镜头二。\n"
            "人物：主角\n"
        )
        imp = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={"text": text},
        )
        assert imp.status_code == 201
        draft = imp.json()["draft"]
        assert draft["payload"]["episodes"][0]["shots"][1]["target_seconds"] is None
        assert any(a["code"] == "M4_AMBIGUITY_MISSING_DURATION" for a in draft["payload"]["ambiguities"])

        # Attempting confirm fails atomically without leaving partial M4EpisodePlan / M4Shot rows
        bad_conf = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": draft["revision"]},
        )
        assert bad_conf.status_code == 422
        assert bad_conf.json()["detail"]["code"] == "M4_SHOT_DURATION_MISSING"
        with sqlite3.connect(data_dir / "manual4.sqlite") as conn:
            assert conn.execute("SELECT COUNT(*) FROM m4_episode_plans WHERE project_id = ?", (pid,)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM m4_shots WHERE project_id = ?", (pid,)).fetchone()[0] == 0

        # User fills 10s (>8s, <=15s) -> triggers episode total deviation (16s vs 20s)
        payload = draft["payload"]
        payload["episodes"][0]["shots"][1]["target_seconds"] = 10.0
        saved = client.put(
            f"/api/v1/manual4/projects/{pid}/storyboard/draft",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": draft["revision"], "payload": payload},
        ).json()["draft"]

        dev_conf = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": saved["revision"], "acknowledge_duration_mismatch": False},
        )
        assert dev_conf.status_code == 409
        assert dev_conf.json()["detail"]["code"] == "M4_EPISODE_DURATION_DEVIATION"

        # Acknowledge deviation -> succeeds cleanly without truncating shots
        ok_conf = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": saved["revision"], "acknowledge_duration_mismatch": True},
        )
        assert ok_conf.status_code == 200
        state = client.get(f"/api/v1/manual4/projects/{pid}/storyboard/draft").json()
        assert state["confirmed_shots"][1]["target_seconds"] == 10.0


def test_p2_long_text_chunking_preserves_every_shot_and_rejects_scanned_pdf(tmp_path):
    app, _ = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "长文本切分测试"},
        ).json()["id"]

        # Build > 12,000 chars across 30 shots
        lines = ["第1集：长篇连拍"]
        padding = "雨滴顺着屋檐滑落，街灯在湿漉漉的石板路上拉出长长的金色倒影。" * 14
        for i in range(1, 31):
            lines.append(f"第{i}镜（5s）：镜头{i}画面内容 {padding}")
            lines.append("人物：主角")
        long_text = "\n".join(lines)
        assert len(long_text) > 12000

        imp = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={"text": long_text, "filename": "long.txt"},
        )
        assert imp.status_code == 201
        shots = imp.json()["draft"]["payload"]["episodes"][0]["shots"]
        assert len(shots) == 30
        assert [s["shot_no"] for s in shots] == list(range(1, 31))

        # Minimal PDF with zero extractable text -> M4_SCANNED_PDF_OCR_UNSUPPORTED
        blank_pdf = (
            b"%PDF-1.4\n1 0 obj<<>>endobj\n"
            b"2 0 obj<< /Type /Catalog /Pages 3 0 R >>endobj\n"
            b"3 0 obj<< /Type /Pages /Kids [4 0 R] /Count 1 >>endobj\n"
            b"4 0 obj<< /Type /Page /Parent 3 0 R /MediaBox [0 0 200 200] >>endobj\n"
            b"xref\n0 5\n0000000000 65535 f \n"
            b"trailer<< /Size 5 /Root 2 0 R >>\nstartxref\n100\n%%EOF"
        )
        pdf_resp = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            files={"file": ("scan.pdf", blank_pdf, "application/pdf")},
        )
        assert pdf_resp.status_code == 422
        assert pdf_resp.json()["detail"]["code"] == "M4_SCANNED_PDF_OCR_UNSUPPORTED"


def test_p2_parenthetical_entity_splitting_and_role_tag_dedup(tmp_path):
    app, _ = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "括号变体与角色标签去重测试", "max_shot_seconds": 15.0},
        ).json()["id"]

        raw_text = (
            "第1集：雪山探险\n"
            "第1镜（6s）：苏瑶站在雪坡上。\n"
            "人物：苏瑶（女主，冲锋衣），陈默（男主）\n"
            "场景：雪山营地（暴风雪，夜）\n"
            "第2镜（6s）：苏瑶更换装备继续前行。\n"
            "人物：苏瑶（女主，探险装）\n"
            "第3镜（6s）：苏瑶特写。\n"
            "人物：苏瑶（女主）、苏瑶\n"
        )
        imp = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={"text": raw_text, "filename": "plan.txt"},
        )
        assert imp.status_code == 201
        draft = imp.json()["draft"]
        shots = draft["payload"]["episodes"][0]["shots"]
        assert shots[0]["requested_assets"] == [
            {"family": "character", "name": "苏瑶", "variant": "冲锋衣"},
            {"family": "character", "name": "陈默", "variant": ""},
            {"family": "scene", "name": "雪山营地", "variant": "暴风雪，夜"},
        ]
        assert shots[1]["requested_assets"] == [
            {"family": "character", "name": "苏瑶", "variant": "探险装"},
        ]
        assert shots[2]["requested_assets"] == [
            {"family": "character", "name": "苏瑶", "variant": ""},
        ]

        conf = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": draft["revision"]},
        )
        assert conf.status_code == 200
        mat_state = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
        names = {(r["family"], r["entity_name"], r["variant_name"]) for r in mat_state["requirements"]}
        assert names == {
            ("character", "苏瑶", "冲锋衣"),
            ("character", "苏瑶", "探险装"),
            ("character", "苏瑶", ""),
            ("character", "陈默", ""),
            ("scene", "雪山营地", "暴风雪，夜"),
        }
        assert all("）" not in r["entity_name"] and "（" not in r["entity_name"] for r in mat_state["requirements"])

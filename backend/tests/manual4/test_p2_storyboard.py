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


def test_p2_intro_variant_curly_quotes_and_broken_bracket_reconciliation(tmp_path):
    from shine.manual4.materials.service import reconcile_m4_confirmed_entities
    from shine.manual4.storage.models import (
        M4AssetVersion,
        M4MaterialRequirement,
        M4Project,
        M4Shot,
        M4ShotAssetBinding,
    )

    app, _ = _setup_m4_client(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/platform/manual4-projects",
            headers=LOCAL_ACTION_HEADERS,
            json={"title": "首镜变体收敛与弯引号对白测试", "max_shot_seconds": 15.0},
        ).json()["id"]

        raw_text = (
            "第1集：大漠遗迹\n"
            "第1镜（8s）：林风与苏瑶在沙丘跋涉。\n"
            "人物：林风（男主，探险装），苏瑶（女主，冲锋衣）\n"
            "场景：敦煌戈壁沙漠\n"
            "第2镜（8s）：林风查看古地图。\n"
            "人物：林风，苏瑶\n"
            "场景：敦煌戈壁沙漠\n"
            "第3镜（8s）：林风低声道：“这就是古地图上的遗迹……”\n"
            "人物：林风，苏瑶\n"
            "场景：敦煌戈壁沙漠\n"
        )
        imp = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/import",
            headers=LOCAL_ACTION_HEADERS,
            data={"text": raw_text, "filename": "script.txt"},
        )
        assert imp.status_code == 201
        shots = imp.json()["draft"]["payload"]["episodes"][0]["shots"]
        # Intro-only outfit variants unify with bare names across Shot 1..3
        assert shots[0]["requested_assets"] == [
            {"family": "character", "name": "林风", "variant": ""},
            {"family": "character", "name": "苏瑶", "variant": ""},
            {"family": "scene", "name": "敦煌戈壁沙漠", "variant": ""},
        ]
        assert shots[1]["requested_assets"] == shots[0]["requested_assets"]
        # Chinese curly quotes in dialogue are parsed cleanly
        assert shots[2]["dialogue"] == [{"speaker": "林风", "text": "这就是古地图上的遗迹……"}]

        conf = client.post(
            f"/api/v1/manual4/projects/{pid}/storyboard/confirm",
            headers=LOCAL_ACTION_HEADERS,
            json={"expected_revision": imp.json()["draft"]["revision"]},
        )
        assert conf.status_code == 200
        mat_state = client.get(f"/api/v1/manual4/projects/{pid}/materials/requirements").json()
        reqs_by_name = {(r["family"], r["entity_name"], r["variant_name"]): r for r in mat_state["requirements"]}
        assert set(reqs_by_name) == {
            ("character", "林风", ""),
            ("character", "苏瑶", ""),
            ("scene", "敦煌戈壁沙漠", ""),
        }
        assert "探险装" in reqs_by_name[("character", "林风", "")]["description"]
        assert "冲锋衣" in reqs_by_name[("character", "苏瑶", "")]["description"]
        assert len(reqs_by_name[("character", "林风", "")]["shot_ids"]) == 3

        # Simulate legacy broken DB state on confirmed Shot 1 (`林风（男主`, `探险装）`) while canonical
        # `林风` & `苏瑶` already have promoted assets, then verify idempotent self-healing.
        with app.state.m4_session_factory.begin() as mdb:
            proj = mdb.get(M4Project, pid)
            assert proj is not None
            sh1 = mdb.scalars(
                mdb.query(M4Shot).statement.where(M4Shot.project_id == pid, M4Shot.shot_code == "ep01-s01")
            ).first()
            assert sh1 is not None
            lin_req = mdb.scalars(
                mdb.query(M4MaterialRequirement).statement.where(
                    M4MaterialRequirement.project_id == pid,
                    M4MaterialRequirement.entity_name == "林风",
                )
            ).first()
            su_req = mdb.scalars(
                mdb.query(M4MaterialRequirement).statement.where(
                    M4MaterialRequirement.project_id == pid,
                    M4MaterialRequirement.entity_name == "苏瑶",
                )
            ).first()
            assert lin_req is not None and su_req is not None

            lin_ast = M4AssetVersion(
                project_id=pid, requirement_id=lin_req.id, family="character", entity_name="林风",
                variant_name="", source_kind="upload", filename="lin.png", content_type="image/png",
                sha256="a" * 64, byte_size=123, storage_path="lin.png", status="promoted",
            )
            su_ast = M4AssetVersion(
                project_id=pid, requirement_id=su_req.id, family="character", entity_name="苏瑶",
                variant_name="", source_kind="upload", filename="su.png", content_type="image/png",
                sha256="b" * 64, byte_size=123, storage_path="su.png", status="promoted",
            )
            broken_ast = M4AssetVersion(
                project_id=pid, requirement_id=lin_req.id, family="character", entity_name="林风（男主",
                variant_name="", source_kind="upload", filename="broken.png", content_type="image/png",
                sha256="c" * 64, byte_size=123, storage_path="broken.png", status="promoted",
            )
            mdb.add_all([lin_ast, su_ast, broken_ast])
            mdb.flush()
            lin_req.bound_asset_version_id = lin_ast.id
            lin_req.status = "bound"
            su_req.bound_asset_version_id = su_ast.id
            su_req.status = "bound"

            orphan_req = M4MaterialRequirement(
                project_id=pid, storyboard_revision_id=lin_req.storyboard_revision_id,
                family="character", entity_name="探险装）", variant_name="", canonical_key="探险装）",
                shot_ids=[sh1.id], status="missing",
            )
            mdb.add(orphan_req)
            sh1.requested_assets = [
                {"family": "character", "name": "林风（男主", "variant": ""},
                {"family": "character", "name": "探险装）", "variant": ""},
                {"family": "character", "name": "苏瑶（女主", "variant": ""},
                {"family": "character", "name": "冲锋衣）", "variant": ""},
                {"family": "scene", "name": "敦煌戈壁沙漠", "variant": ""},
            ]
            mdb.add(
                M4ShotAssetBinding(
                    project_id=pid, shot_id=sh1.id, asset_version_id=broken_ast.id,
                    use_as="image", role_label="林风（男主", ordinal=0,
                )
            )
            mdb.flush()
            assert reconcile_m4_confirmed_entities(mdb, proj) is True

        healed = client.get(f"/api/v1/manual4/projects/{pid}/production/state").json()
        s1_manifest = healed["episodes"][0]["shots"][0]["reference_manifest"]
        assert [(m["h3_label"], m["role_label"]) for m in s1_manifest] == [
            ("<Subject 1>", "林风"),
            ("<Subject 2>", "苏瑶"),
        ]


"""V4 M2 tests: modular action handlers, 39 project write capabilities, project-level grants."""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from shine.copilot import declarations as decl
from shine.copilot import task_grant as grant_contract
from shine.copilot import tool_loop, tool_registry
from shine.director.models import DirectorPackageVersion
from shine.llm.tool_calls import ToolCall, ToolTurn
from shine.media.output_layout import output_root
from shine.models import (
    Asset,
    Job,
    Project,
    Shot,
    ShotAssetBinding,
    StyleVersion,
    new_id,
)
from shine.production.models import ProductionRun
from shine.schemas import ShotSpec
from shine.support import action_handlers
from shine.support import actions as action_service
from shine.support import grants as grant_service
from tests.copilot_v4_support import HEADERS, ScriptedGateway, configure_mock_text_model

pytestmark = pytest.mark.integration


def _create_project(client, title: str = "V4 动作测试项目") -> str:
    resp = client.post("/api/v1/projects", json={"title": title, "language": "Chinese"})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _open_session(client, project_id: str | None = None) -> dict:
    resp = client.post(
        "/api/v1/support/sessions",
        json={"page_id": "director", "project_id": project_id, "question": ""},
        headers=HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session"]


def _preview_and_approve(client, kind: str, params: dict, *, session_id: str | None = None, project_id: str | None = None):
    prev = client.post(
        "/api/v1/support/actions/preview",
        json={"kind": kind, "params": params, "session_id": session_id, "project_id": project_id},
        headers=HEADERS,
    )
    assert prev.status_code == 200, prev.text
    action = prev.json()["action"]
    app = client.post(
        f"/api/v1/support/actions/{action['id']}/approve",
        json={"preview_hash": action["preview_hash"], "confirm": True},
        headers=HEADERS,
    )
    return app


def _seed_shot_with_style(factory, project_id: str, *, dialogue_end_s: float = 5.0) -> tuple[str, str]:
    style_id = new_id()
    shot_id = new_id()
    with factory() as db:
        style = StyleVersion(
            id=style_id,
            project_id=project_id,
            version=1,
            snapshot={
                "name": "默认写实",
                "prompt_prefix": "natural realism",
                "negative_prompt": "watermark",
                "camera_language": "35mm",
                "color_palette": "natural",
            },
            sha256=new_id().replace("-", "") * 2,
            approved_by="local-user",
        )
        db.add(style)
        project = db.get(Project, project_id)
        project.frozen_style_version_id = style_id
        spec = ShotSpec(
            story_beat="夜色中的低语对峙",
            characters=["主角"],
            duration_target_s=6.0,
            style_version_id=style_id,
            dialogue=[{"speaker_id": "主角", "text": "你终于来了。", "start_s": 0.5, "end_s": dialogue_end_s}],
        ).model_dump(mode="json")
        shot = Shot(
            id=shot_id,
            project_id=project_id,
            name="第 8 镜 圣殿夜话",
            revision=1,
            spec=spec,
        )
        db.add(shot)
        db.commit()
    return style_id, shot_id


def test_v4_actions_declared_registered_and_redlines_intact():
    write_ids = {row.action_id for row in decl.WRITE_ACTIONS}
    assert len(decl.WRITE_ACTIONS) == 40
    assert write_ids == set(action_handlers.REGISTRY)
    assert write_ids <= set(grant_contract.CAPABILITY_PERMISSIONS)
    assert write_ids <= set(tool_registry.AUTONOMOUS_CAPABILITIES)
    assert "acceptance_status" not in write_ids
    for cap_id, perm in grant_contract.CAPABILITY_PERMISSIONS.items():
        assert perm not in grant_contract.PERMANENTLY_FORBIDDEN, cap_id


def test_v4_literary_shot_revise_enforces_dialogue_guard_and_appends_receipt(api):
    client, factory, _ = api
    project_id = _create_project(client, "修订单镜测试")
    _, shot_id = _seed_shot_with_style(factory, project_id, dialogue_end_s=5.0)
    session = _open_session(client, project_id)

    # Dialogue ends at 5.0s → revising down to 4.0s must be refused by the original gate.
    refused = _preview_and_approve(
        client,
        "shot_revise",
        {"project_id": project_id, "shot_id": shot_id, "duration_target_s": 4.0},
        session_id=session["id"],
        project_id=project_id,
    )
    assert refused.status_code == 423
    assert refused.json()["detail"]["code"] == "SHOT_DURATION_BELOW_DIALOGUE"

    # Valid revision succeeds, bumps revision, and feeds an action_receipt message back into the session.
    ok = _preview_and_approve(
        client,
        "shot_revise",
        {"project_id": project_id, "shot_id": shot_id, "duration_target_s": 7.0, "continuity_mode": "cut"},
        session_id=session["id"],
        project_id=project_id,
    )
    assert ok.status_code == 200, ok.text
    action = ok.json()["action"]
    assert action["status"] == "SUCCEEDED"
    assert action["verification"]["new_revision"] == 2

    with factory() as db:
        shot = db.get(Shot, shot_id)
        assert shot.revision == 2
        assert shot.spec["duration_target_s"] == 7.0

    fetched = client.get(f"/api/v1/support/sessions/{session['id']}")
    assert fetched.status_code == 200
    receipt_msgs = [m for m in fetched.json()["messages"] if m.get("kind") == "action_receipt"]
    assert any((m.get("payload") or {}).get("action_receipt", {}).get("action_id") == action["id"] for m in receipt_msgs)


def test_v4_materials_preferences_and_d12_shot_swap(api):
    client, factory, settings = api
    project_id = _create_project(client, "素材与换图测试")
    _, shot_id = _seed_shot_with_style(factory, project_id)
    session = _open_session(client, project_id)

    # Toggle character_sheet_mode on and verify persisted state.
    toggled = _preview_and_approve(
        client,
        "material_preferences_update",
        {"project_id": project_id, "character_sheet_mode": True},
        session_id=session["id"],
        project_id=project_id,
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["action"]["status"] == "SUCCEEDED"
    progress = client.get(f"/api/v1/projects/{project_id}/material-preparation").json()
    assert progress["preferences"]["character_sheet_mode"] is True

    # Seed two locked scene masters + bind old one to shot, then execute D12 shot_swap_material_binding.
    old_bytes = b"OLD_SCENE_IMAGE_BYTES"
    new_bytes = b"NEW_SCENE_IMAGE_BYTES"
    with factory() as db:
        root = Path(output_root(db, settings))
        old_rel = f"projects/{project_id}/assets/scene_old.png"
        new_rel = f"projects/{project_id}/assets/scene_new.png"
        (root / old_rel).parent.mkdir(parents=True, exist_ok=True)
        (root / old_rel).write_bytes(old_bytes)
        (root / new_rel).write_bytes(new_bytes)

        old_asset = Asset(
            id=new_id(), project_id=project_id, kind="image", role="scene", entity_id="圣殿",
            content_type="image/png", filename="scene_old.png", storage_path=old_rel,
            sha256=hashlib.sha256(old_bytes).hexdigest(), byte_size=len(old_bytes),
            approved=True, locked=True, approved_by="local-user",
        )
        new_asset = Asset(
            id=new_id(), project_id=project_id, kind="image", role="scene", entity_id="圣殿",
            content_type="image/png", filename="scene_new.png", storage_path=new_rel,
            sha256=hashlib.sha256(new_bytes).hexdigest(), byte_size=len(new_bytes),
            approved=True, locked=True, approved_by="local-user",
        )
        db.add_all([old_asset, new_asset])
        db.flush()
        db.add(ShotAssetBinding(shot_id=shot_id, asset_id=old_asset.id, use_as="picture", role=None, ordinal=0))
        shot = db.get(Shot, shot_id)
        shot.spec = {**shot.spec, "required_asset_ids": [old_asset.id]}
        new_asset_id = new_asset.id
        db.commit()

    swapped = _preview_and_approve(
        client,
        "shot_swap_material_binding",
        {
            "project_id": project_id,
            "shot_ids": [shot_id],
            "project_asset_id": new_asset_id,
            "role": "scene",
            "entity_id": "圣殿",
            "allow_revise_frozen": True,
        },
        session_id=session["id"],
        project_id=project_id,
    )
    assert swapped.status_code == 200, swapped.text
    act = swapped.json()["action"]
    assert act["status"] == "SUCCEEDED"
    assert act["verification"]["adopted_count"] == 1

    with factory() as db:
        shot = db.get(Shot, shot_id)
        assert shot.revision == 2
        assert [b.asset_id for b in shot.bindings] == [new_asset_id]


def test_v4_asset_center_lora_lifecycle(api, tmp_path):
    client, _, _ = api
    session = _open_session(client)

    header = json.dumps({
        "__metadata__": {"ss_network_module": "networks.lora"},
        "lora_unet.weight": {"dtype": "F16", "shape": [2, 2], "data_offsets": [0, 8]},
    }).encode("utf-8")
    lora_file = tmp_path / "test_style.safetensors"
    lora_file.write_bytes(struct.pack("<Q", len(header)) + header + b"\x00" * 8)

    imported = _preview_and_approve(
        client,
        "lora_import_local",
        {
            "local_path": str(lora_file),
            "display_name": "测试画风",
            "default_strength": 0.6,
            "trigger_word": "oilpaint",
            "base_model": "qwen_image_21",
        },
        session_id=session["id"],
    )
    assert imported.status_code == 200, imported.text
    lora_id = imported.json()["action"]["verification"]["lora_id"]
    assert lora_id

    updated = _preview_and_approve(
        client,
        "lora_update",
        {"lora_id": lora_id, "display_name": "油画厚涂", "default_strength": 0.75},
        session_id=session["id"],
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["action"]["verification"]["lora"]["default_strength"] == 0.75

    deleted = _preview_and_approve(
        client,
        "lora_delete",
        {"lora_id": lora_id},
        session_id=session["id"],
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["action"]["status"] == "SUCCEEDED"


def test_v4_reconcile_never_resubmits_unknown_and_cross_project_rejected(api):
    client, factory, _ = api
    project_a = _create_project(client, "项目甲")
    project_b = _create_project(client, "项目乙")
    _, shot_a = _seed_shot_with_style(factory, project_a)
    session_b = _open_session(client, project_b)

    # Cross-project preview from session_b targeting project_a must be refused cleanly.
    cross = client.post(
        "/api/v1/support/actions/preview",
        json={
            "kind": "shot_revise",
            "params": {"project_id": project_a, "shot_id": shot_a, "duration_target_s": 7.0},
            "session_id": session_b["id"],
        },
        headers=HEADERS,
    )
    assert cross.status_code == 400
    assert cross.json()["detail"]["code"] == "INVALID_ACTION_PARAMS"

    # task_reconcile reconciles a NEEDS_REVIEW run without creating any duplicate job.
    with factory() as db:
        pkg_id = new_id()
        db.add(DirectorPackageVersion(
            id=pkg_id,
            project_id=project_a,
            revision=1,
            status="CONFIRMED",
            manifest={"shots": [shot_a]},
            scope_digest="s" * 64,
            manifest_sha256="m" * 64,
            policy_hash="p" * 64,
            episode_count=1,
            shot_count=1,
            stale=False,
        ))
        db.flush()
        run_id = new_id()
        db.add(ProductionRun(id=run_id, project_id=project_a, package_id=pkg_id, package_revision=1, status="NEEDS_REVIEW", scope_digest="d" * 64, manifest_sha256="m" * 64, policy_hash="p" * 64, idempotency_key=f"idem:{run_id}", request_hash="r" * 64))
        before_jobs = db.query(Job).count()
        db.commit()

    session_a = _open_session(client, project_a)
    reconciled = _preview_and_approve(
        client,
        "task_reconcile",
        {"run_id": run_id, "project_id": project_a},
        session_id=session_a["id"],
        project_id=project_a,
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["action"]["status"] == "SUCCEEDED"
    with factory() as db:
        assert db.query(Job).count() == before_jobs


def test_v4_project_grant_tool_loop_multistep_and_gpu_off_guard(api):
    client, factory, settings = api
    configure_mock_text_model(client)
    project_id = _create_project(client, "多步授权闭环")
    _, shot_id = _seed_shot_with_style(factory, project_id)
    session = _open_session(client, project_id)

    prev = client.post(
        "/api/v1/support/task-grants/preview",
        json={
            "session_id": session["id"],
            "project_id": project_id,
            "objective": "开启五视图模式并把第8镜改为7秒",
            "capability_groups": ["inspect", "literary", "materials", "test"],
            "allow_gpu": False,
            "allow_download": False,
        },
        headers=HEADERS,
    )
    assert prev.status_code == 200, prev.text
    body = prev.json()
    assert body["can_authorize"] is True
    assert body["limits"]["max_test_inference_jobs"] == 0
    assert body["limits"]["max_download_bytes"] == 0
    assert "material_generate" not in body["capabilities"]
    assert "shot_revise" in body["capabilities"]
    assert "material_preferences_update" in body["capabilities"]

    auth = client.post(
        "/api/v1/support/task-grants/authorize",
        json={
            "session_id": session["id"],
            "preview_id": body["preview_id"],
            "grant_digest": body["grant_digest"],
            "confirm": True,
            "run": False,
        },
        headers=HEADERS,
    )
    assert auth.status_code == 200, auth.text
    grant_id = auth.json()["grant_id"]

    # GPU action must be blocked because allow_gpu=False excluded 'test'.
    with factory() as db:
        refused = action_service.execute_under_grant(
            db, settings,
            grant_id=grant_id,
            capability_id="material_generate",
            params={"project_id": project_id, "requirement_ids": ["req-1"]},
            tool_call_id="tc-gpu-denied",
            actor="local-user",
            session_id=session["id"],
        )
        assert refused["status"] == "BLOCKED"
        assert refused["error_code"] == "GRANT_PERMISSION_NOT_GRANTED"

    # Drive multi-step tool_loop across two domains inside one project grant.
    gw = ScriptedGateway([
        ToolTurn(
            text="step 1",
            tool_calls=[ToolCall(id="tc-step-1", name="material_preferences_update", arguments={"project_id": project_id, "character_sheet_mode": True})],
            native=False,
        ),
        ToolTurn(
            text="step 2",
            tool_calls=[ToolCall(id="tc-step-2", name="shot_revise", arguments={"project_id": project_id, "shot_id": shot_id, "duration_target_s": 7.5})],
            native=False,
        ),
        ToolTurn(text="已完成两项项目操作：五视图已开启，镜头修订为 7.5 秒。", tool_calls=[], native=False),
    ])
    with factory() as db:
        loaded = grant_service.load(db, grant_id)
        res = tool_loop.advance(
            db,
            settings,
            grant_id=grant_id,
            objective="开启五视图模式并把第8镜改为7.5秒",
            tools=tool_registry.grant_tool_specs(loaded.grant),
            session_id=session["id"],
            gateway=gw,
            max_steps=6,
            owner="test-v4-multistep",
        )
        assert res.stop_reason == "MODEL_FINISHED_WITH_EVIDENCE"
        loaded_after = grant_service.load(db, grant_id)
        sub = grant_service.sub_action_public(loaded_after.grant)
        assert len(sub) == 2

    with factory() as db:
        shot = db.get(Shot, shot_id)
        assert shot.revision == 2
        assert shot.spec["duration_target_s"] == 7.5
